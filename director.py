#!/usr/bin/env python3
"""nwn-director: film the areas of a Neverwinter Nights: Enhanced Edition module.

    director.py film MODULE.mod [--areas AREA[,AREA...]] [--spot AREA@X,Y[/FACING]|AREA@TAG ...]
                                [--event AREA@X,Y=CODE|AREA@TAG=CODE ...] [--stills N]
                                --character CHAR.bic [options]
    director.py areas MODULE.mod

`film` copies the module with nwn-director's script in it, runs the copy on a local server
(Docker), logs your game client in as the cameraman and records every shot to OUT/<name>.mp4:
a walkthrough of each area in --areas (<area>.mp4), a slow circle around each --spot
(<area>-<x>-<y>.mp4 or <area>-<tag>.mp4), and the same for each --event, with its CODE run a
few seconds in (<area>-<x>-<y>-event.mp4 or <area>-<tag>-event.mp4). An AREA is a resref or an
area's tag (as bug reports give it). With --stills N, spots are N stills instead of a video
(<name>-00.png ... and a contact sheet <name>.png), from a client drawing on the CPU in its own
virtual display: no GPU, and your desktop is left alone.
`areas` lists the module's areas (resref, tag, size, exits) to choose from.

See README.md for requirements and how it works.
"""
import argparse
import concurrent.futures
import datetime
import glob
import json
import math
import os
import queue
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONTAINER = 'nwn-director'
SERVER_IMAGE = 'ghcr.io/urothis/nwserver:8193.37.17'
GAME_DIRS = [
    '~/.local/share/Steam/steamapps/common/Neverwinter Nights',
    '~/.steam/steam/steamapps/common/Neverwinter Nights',
    '~/GOG Games/Neverwinter Nights Enhanced Edition/game',
    '~/Beamdog Library/00829',
]
USER_DIR = '~/.local/share/Neverwinter Nights'

# The cameraman client's settings.tml: the player's own, with these changed. (section, key) -> value
CLIENT_SETTINGS = {
    ('debug.panel', 'visible'): 'false',
    ('game.character-generation', 'show-pregenerated'): 'false',
    ('graphics.window', 'mode'): '"borderless"',
    ('graphics.intro.splash', 'enabled'): 'false',
    ('graphics.movies.intro', 'enabled'): 'false',
    ('graphics.hilite', 'enabled'): 'false',
    ('graphics.general', 'shader-quality'): '"High Quality"',
    ('graphics.effects', 'high-quality'): 'true',
    ('graphics.lighting', 'enhanced'): 'true',
    ('graphics.grass', 'mode'): '2',
    ('graphics.shadows.creatures', 'mode'): '2',
    ('graphics.shadows.environment', 'enabled'): 'true',
    ('graphics.skyboxes', 'enabled'): 'true',
    ('graphics.texture-animations', 'enabled'): 'true',
    ('graphics.water', 'shiny'): 'true',
    ('graphics.video.anti-aliasing', 'mode'): '4',
    ('graphics.video.anisotropic-filtering', 'mode'): '8',
    ('sound.effects', 'volume'): '1.0',
    ('sound.music', 'volume'): '1.0',
    ('sound.streams', 'volume'): '1.0',
    ('sound.voices', 'volume'): '1.0',
}

# Stills: the client draws on the CPU, where every pixel costs (on a busy machine a frame of a big town took so long
# that the server dropped the client). Anti-aliasing is the dearest setting there and changes little in a still; then
# the shaders, lighting, grass, shadows and screen effects. A frame limit leaves the CPU between frames to the network
# and loading. With STILLS_SIZE, a still takes seconds instead of minutes.
STILLS_SETTINGS = {
    ('graphics.video.anti-aliasing', 'mode'): '0',
    ('graphics.video.anisotropic-filtering', 'mode'): '0',
    ('graphics.general', 'shader-quality'): '"Performance"',
    ('graphics.effects', 'high-quality'): 'false',
    ('graphics.lighting', 'enhanced'): 'false',
    ('graphics.texture-animations', 'enabled'): 'false',
    ('graphics.grass', 'mode'): '0',
    ('graphics.shadows.creatures', 'mode'): '0',
    ('graphics.shadows.environment', 'enabled'): 'false',
    ('graphics.fbo.ssao', 'enabled'): 'false',
    ('graphics.fbo.dof', 'enabled'): 'false',
    ('graphics.fbo.hdr-bloom', 'enabled'): 'false',
    ('graphics.water', 'shiny'): 'false',
    ('graphics.lod', 'enabled'): 'true',
    ('graphics.framelimiter', 'enabled'): 'true',
    ('graphics.framelimiter.foreground', 'target-fps'): '10',
    ('graphics.framelimiter.background', 'target-fps'): '10',
}

# Only in a settings.tml made from nothing (no player's file: a server or CI runner where nobody has played). A
# player's client has a name (from Steam, or asked once and kept); without one it stops at "Please Enter a Player
# Name" before joining.
NEW_SETTINGS = {
    ('client.identity', 'name'): '"director"',
}


def log(msg):
    print(f'director: {msg}', flush=True)


def die(msg):
    sys.exit(f'director: {msg}')


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


# ---------------------------------------------------------------- reading the module

def unpack(mod, into):
    os.makedirs(into, exist_ok=True)
    run(['nwn_erf', '--quiet', '-x', '-f', os.path.abspath(mod)], cwd=into)


def gff(path):
    """A GFF file as nwn_gff's JSON."""
    out = subprocess.run(['nwn_gff', '--quiet', '-i', path, '-k', 'json'], check=True,
                         capture_output=True).stdout
    return json.loads(out)


def area_files(unpacked):
    """{resref: path without extension} for every area (.are with its .git) in an unpacked module."""
    areas = {}
    for are in sorted(glob.glob(os.path.join(unpacked, '*.are'))):
        if os.path.exists(are[:-4] + '.git'):
            areas[os.path.basename(are)[:-4].lower()] = are[:-4]
    return areas


def area_by_name(name, areas, tags):
    """The resref of the area called name: a resref, or else an area's tag (tags maps resref to
    tag, read on first use). Dies if there is none, or if the tag is on more than one area."""
    if name.lower() in areas:
        return name.lower()
    if not tags:
        log('looking areas up by tag')
        with concurrent.futures.ThreadPoolExecutor(os.cpu_count() or 4) as pool:
            found = pool.map(lambda base: gff(base + '.are').get('Tag', {}).get('value', ''), areas.values())
            tags.update(zip(areas.keys(), found))
    found = [r for r, t in tags.items() if t == name] or [r for r, t in tags.items() if t.lower() == name.lower()]
    if not found:
        die(f'no area {name} in the module, by resref or tag (director.py areas MODULE lists them)')
    if len(found) > 1:
        die(f'more than one area has the tag {name}: {", ".join(found)}; give the resref')
    return found[0]


def exits(git):
    """Every transition in an area: doors and triggers with a destination. A trigger's point is
    the middle of its shape. A door's is 2.5 m in front of it, with the spot 2.5 m behind it as
    the second choice (x2, y2): nothing can walk to or from the door itself, and only one side of
    it is in this area (dir_load takes the walkable one)."""
    pts = []
    for d in git.get('Door List', {}).get('value', []):
        if d.get('LinkedTo', {}).get('value'):
            x, y, b = d['X']['value'], d['Y']['value'], d['Bearing']['value']
            dx, dy = 2.5 * math.cos(b), 2.5 * math.sin(b)
            pts.append((x + dx, y + dy, x - dx, y - dy))
    for t in git.get('TriggerList', {}).get('value', []):
        if not t.get('LinkedTo', {}).get('value'):
            continue
        geo = t.get('Geometry', {}).get('value', [])
        x, y = t['XPosition']['value'], t['YPosition']['value']
        if geo:
            x += sum(p['PointX']['value'] for p in geo) / len(geo)
            y += sum(p['PointY']['value'] for p in geo) / len(geo)
        pts.append((x, y))
    return pts


def where(p):
    return p[:2]


# A spot sets off only the encounters this close (m): farther ones can't be in its shot, and the cameraman
# stepping into each takes a second (more on a slow client, which draws every place it goes).
SPOT_ENCOUNTER_RANGE = 40.0


def encounters(git, near=None):
    """The middle of every encounter trigger (within SPOT_ENCOUNTER_RANGE of near, if given): the
    cameraman steps into each before filming, so the area's monsters and wildlife spawn."""
    out = []
    for e in git.get('Encounter List', {}).get('value', []):
        geo = e.get('Geometry', {}).get('value', [])
        if geo:
            pt = [round(e['XPosition']['value'] + sum(p['X']['value'] for p in geo) / len(geo), 2),
                  round(e['YPosition']['value'] + sum(p['Y']['value'] for p in geo) / len(geo), 2)]
            if near is None or math.dist(pt, near) <= SPOT_ENCOUNTER_RANGE:
                out.append(pt)
    return out


def tagged_position(git, tag):
    """(x, y) of the first object tagged tag in an area's .git, or None (placed by a script, say)."""
    lists = {'Creature List': ('XPosition', 'YPosition'), 'Placeable List': ('X', 'Y'), 'Door List': ('X', 'Y'),
             'WaypointList': ('XPosition', 'YPosition'), 'StoreList': ('XPosition', 'YPosition'),
             'TriggerList': ('XPosition', 'YPosition'), 'SoundList': ('XPosition', 'YPosition'),
             'Encounter List': ('XPosition', 'YPosition')}
    for name, (kx, ky) in lists.items():
        for o in git.get(name, {}).get('value', []):
            if o.get('Tag', {}).get('value') == tag and kx in o:
                return (o[kx]['value'], o[ky]['value'])
    return None


def route(git):
    """The walker's stops in an area, nearest first from its first exit: every exit, placed
    creature and encounter spawn point. Empty if the area has no exits."""
    pts = exits(git)
    if not pts:
        return []
    for c in git.get('Creature List', {}).get('value', []):
        pts.append((c['XPosition']['value'], c['YPosition']['value']))
    for e in git.get('Encounter List', {}).get('value', []):
        for sp in e.get('SpawnPointList', {}).get('value', []):
            pts.append((sp['X']['value'], sp['Y']['value']))
    # Stops within 5 m of one already kept add nothing (a herd, a pair of doors).
    kept = []
    for p in pts:
        if all(math.dist(where(p), where(k)) >= 5.0 for k in kept):
            kept.append(p)
    stops, rest = [kept[0]], kept[1:]
    while rest:
        nxt = min(rest, key=lambda p: math.dist(where(p), where(stops[-1])))
        rest.remove(nxt)
        stops.append(nxt)
    return [[round(v, 2) for v in p] for p in stops]


def walk_shot(area, git):
    """A walkthrough of an area (DIRECTOR_SHOTS entry)."""
    return {'name': area, 'area': area, 'kind': 'walk', 'stops': route(git), 'encounters': encounters(git)}


def spot_shot(area, git, where_, event=None):
    """A shot circling one place in an area: where_ is "X,Y" or an object's tag, with "/FACING"
    after it to start the circle looking that way (degrees, 0 east, 90 north). An event (a script
    name, or NWScript statements) runs a few seconds in."""
    shot = {'area': area, 'kind': 'spot'}
    m = re.fullmatch(r'(.*?)\s*/\s*(-?[0-9.]+)\s*', where_)
    if m:
        where_ = m.group(1)
        shot['facing'] = round(float(m.group(2)) % 360.0, 1)
    m = re.fullmatch(r'\s*(-?[0-9.]+)\s*,\s*(-?[0-9.]+)\s*', where_)
    if m:
        shot['at'] = [float(m.group(1)), float(m.group(2))]
        shot['name'] = f'{area}-{round(float(m.group(1)))}-{round(float(m.group(2)))}'
    else:
        shot['tag'] = where_
        shot['name'] = f'{area}-{re.sub(r"[^A-Za-z0-9_-]", "", where_).lower()}'
    near = shot['at'] if 'at' in shot else tagged_position(git, shot['tag'])
    shot['encounters'] = encounters(git, near)
    if event:
        shot['event'] = event
        shot['name'] += '-event'
    return shot


# ---------------------------------------------------------------- the module copy

def set_var(ifo, name, kind, value):
    """Set a module variable in module.ifo's VarTable. kind: 1 int, 2 float, 3 string."""
    table = ifo.setdefault('VarTable', {'type': 'list', 'value': []})['value']
    table[:] = [v for v in table if v['Name']['value'] != name]
    vtype = {1: 'int', 2: 'float', 3: 'cexostring'}[kind]
    table.append({'__struct_id': 0,
                  'Name': {'type': 'cexostring', 'value': name},
                  'Type': {'type': 'dword', 'value': kind},
                  'Value': {'type': vtype, 'value': value}})


def add_anchor(game, work, git_path, x, y):
    """Place the anchor in an area: an invisible, scriptless creature (the base game's nw_commale)
    whose spawn script is dir_load, so the director starts while the module loads even if a
    framework takes over the module's own events first."""
    run(['nwn_resman_extract', '--quiet', '--root', game, '--no-ovr', '-d', work, 'nw_commale.utc'])
    anchor = gff(os.path.join(work, 'nw_commale.utc'))
    anchor.pop('__data_type', None)
    for key in list(anchor):
        if key.startswith('Script'):
            anchor[key]['value'] = ''
    anchor['ScriptSpawn']['value'] = 'dir_load'
    anchor['Appearance_Type']['value'] = 298
    anchor['Tag']['value'] = 'DIRECTOR_ANCHOR'
    anchor['Conversation']['value'] = ''
    anchor['__struct_id'] = 4
    for key, value in (('XPosition', x), ('YPosition', y), ('ZPosition', 0.0),
                       ('XOrientation', 1.0), ('YOrientation', 0.0)):
        anchor[key] = {'type': 'float', 'value': float(value)}
    git = gff(git_path)
    git.setdefault('Creature List', {'type': 'list', 'value': []})['value'].append(anchor)
    write_gff(git, git_path)


def write_gff(data, path):
    with open(path + '.json', 'w') as f:
        json.dump(data, f)
    run(['nwn_gff', '--quiet', '-i', path + '.json', '-l', 'json', '-o', path, '-k', 'gff'])
    os.remove(path + '.json')


def compile_event(game, unpacked, event, n):
    """An event's script: a script the module already has, by name, or the event's NWScript
    statements compiled now (against the base game, as void main() { ... }) into dir_ev<n>, so a
    mistake stops here with the compiler's message rather than in the middle of filming."""
    if re.fullmatch(r'[A-Za-z0-9_]{1,16}', event):
        if not os.path.exists(os.path.join(unpacked, event.lower() + '.ncs')):
            die(f'--event: the module has no compiled script named {event}')
        return event.lower()
    name = f'dir_ev{n}'
    src = os.path.join(unpacked, name + '.nss')
    with open(src, 'w') as f:
        f.write('void main()\n{\n' + event + '\n}\n')
    result = subprocess.run(['nwn_script_comp', '--quiet', '--root', game, '-o',
                             os.path.join(unpacked, name + '.ncs'), src], capture_output=True, text=True)
    os.remove(src)
    if result.returncode != 0 or not os.path.exists(os.path.join(unpacked, name + '.ncs')):
        die(f'--event code does not compile:\n  {event}\n' + (result.stdout + result.stderr).strip())
    return name


def prepare(args, game, work):
    """Copy the module with dir_load as its OnModuleLoad and the walkthroughs in module variables.
    Returns the copy's path."""
    unpacked = os.path.join(work, 'module')
    log(f'unpacking {args.module}')
    unpack(args.module, unpacked)
    ours = [os.path.basename(f) for f in glob.glob(os.path.join(unpacked, 'dir_*.ncs'))
            if re.fullmatch(r'dir_(load|ev\d+)\.ncs', os.path.basename(f))]
    if ours:
        die(f'the module already has scripts named like nwn-director\'s: {", ".join(ours)}')

    areas = area_files(unpacked)
    tags = {}
    shots = []
    for spec in args.shot_specs:
        kind, area, where_, event = spec
        area = area_by_name(area, areas, tags)
        git = gff(areas[area] + '.git')
        if event:
            event = compile_event(game, unpacked, event, len(shots))
        shot = walk_shot(area, git) if kind == 'walk' else spot_shot(area, git, where_, event)
        if kind == 'walk' and len(shot['stops']) < 2:
            log(f'{area} has no exits to walk between; it will be skipped')
        shots.append(shot)

    ifo_path = os.path.join(unpacked, 'module.ifo')
    ifo = gff(ifo_path)
    haks = [h['Mod_Hak']['value'] for h in ifo.get('Mod_HakList', {}).get('value', [])]
    missing = [h for h in haks if not os.path.exists(os.path.join(args.hak_dir, h + '.hak'))]
    if missing:
        die(f'haks the module needs are not in {args.hak_dir}: {", ".join(missing)} (--hak-dir)')
    set_var(ifo, 'DIRECTOR_ONLOAD', 3, ifo.get('Mod_OnModLoad', {}).get('value', ''))
    set_var(ifo, 'DIRECTOR_SHOTS', 3, json.dumps(shots, separators=(',', ':')))
    set_var(ifo, 'DIRECTOR_SECONDS', 2, float(args.seconds))
    set_var(ifo, 'DIRECTOR_RUN', 1, 1 if args.run else 0)
    set_var(ifo, 'DIRECTOR_STILLS', 1, args.stills)
    ifo['Mod_OnModLoad'] = {'type': 'resref', 'value': 'dir_load'}
    write_gff(ifo, ifo_path)
    # The anchor only has to spawn: the middle of the first shot's area will do.
    are = gff(areas[shots[0]['area']] + '.are')
    add_anchor(game, work, areas[shots[0]['area']] + '.git',
               are['Width']['value'] * 5.0, are['Height']['value'] * 5.0)

    run(['nwn_script_comp', '--quiet', '--root', game, '-o', os.path.join(unpacked, 'dir_load.ncs'),
         os.path.join(HERE, 'nss', 'dir_load.nss')])

    home = os.path.join(work, 'server')
    os.makedirs(os.path.join(home, 'modules'))
    if args.stills:
        # The count of stills saved, which dir_load reads (SqlPrepareQueryCampaign "director") to
        # know when to turn to the next angle. Writable by the server's user too.
        db_dir = os.path.join(home, 'database')
        os.makedirs(db_dir)
        os.chmod(db_dir, 0o777)
        with sqlite3.connect(os.path.join(db_dir, 'director.sqlite3')) as db:
            db.execute('CREATE TABLE stills (n INTEGER)')
            db.execute('INSERT INTO stills VALUES (0)')
        os.chmod(os.path.join(db_dir, 'director.sqlite3'), 0o666)
    if args.script_log:
        # Every script run goes to the server log (docker logs nwn-director), for finding what a
        # module's own scripts do to the walker.
        with open(os.path.join(home, 'settings.tml'), 'w') as f:
            f.write('[nwscript]\n\t[nwscript.log]\n\t\t[nwscript.log.script-execution]\n\t\t\tenabled = true\n')
    mod = os.path.join(home, 'modules', 'director.mod')
    log(f'packing the copy ({len(shots)} shots)')
    # The folder, not its files: a big module's file list can pass the kernel's limit on a command line (15,800
    # files and 1.8 MB for one of 164 MB), and packing a folder is twice as fast.
    run(['nwn_erf', '--quiet', '-c', '-e', 'MOD', '-f', mod, unpacked])
    return home


# ---------------------------------------------------------------- server

def start_server(args, home):
    subprocess.run(['docker', 'rm', '-f', CONTAINER], capture_output=True)
    cmd = ['docker', 'run', '-d', '--name', CONTAINER,
           '-p', f'127.0.0.1:{args.port}:{args.port}/udp',
           '-v', f'{home}:/nwn/home',
           '-v', f'{os.path.realpath(args.hak_dir)}:/nwn/home/hak:ro',
           '-v', f'{os.path.realpath(args.tlk_dir)}:/nwn/home/tlk:ro',
           '-e', 'NWN_MODULE=director', '-e', f'NWN_PORT={args.port}',
           '-e', 'NWN_SERVERNAME=nwn-director', '-e', 'NWN_PUBLICSERVER=0',
           '-e', 'NWN_SERVERVAULT=0', '-e', 'NWN_ELC=0', '-e', 'NWN_ILR=0',
           '-e', 'NWN_PAUSEANDPLAY=0', '-e', 'NWN_MAXLEVEL=40',
           args.image]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        die(f'the server did not start: {result.stderr.strip()} (is something else on port {args.port}? --port)')


class ServerLog:
    """The server's log, line by line, as the image mirrors it to stdout. Read on a thread, so
    waiting for a line can time out."""

    def __init__(self):
        self.proc = subprocess.Popen(['docker', 'logs', '-f', CONTAINER], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, errors='replace')
        self.queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.queue.put(line.strip())
        self.queue.put(None)

    def next(self, timeout):
        """The next line (maybe blank), or None if none came within timeout seconds."""
        try:
            line = self.queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is None:
            die('the server stopped; see: docker logs nwn-director')
        return line

    def wait_for(self, pattern, timeout, fatal=True):
        end = time.time() + timeout
        while time.time() < end:
            line = self.next(end - time.time())
            if line is not None and re.search(pattern, line):
                return line
        if not fatal:
            return None
        die(f'timed out waiting for "{pattern}" in the server log (docker logs nwn-director)')

    def lines(self, quiet_timeout):
        """Lines until none comes for quiet_timeout seconds."""
        while True:
            line = self.next(quiet_timeout)
            if line is None:
                die(f'the server log went quiet for {quiet_timeout} s (docker logs nwn-director)')
            yield line

    def close(self):
        self.proc.kill()


# ---------------------------------------------------------------- client

def client_settings(src, dst, settings):
    """Write the cameraman client's settings.tml: the player's own (src) with settings ((section, key) -> TOML value)
    changed line by line, so the rest of the file stays as the client wrote it. A key the file lacks goes under its
    section's header, or in a new section at the end. With no src (nobody has played here), a file of settings and
    NEW_SETTINGS: the client takes defaults for the rest and fills them in."""
    if not src or not os.path.exists(src):
        settings = {**NEW_SETTINGS, **settings}
        with open(dst, 'w', encoding='utf-8') as f:
            for section in dict.fromkeys(s for s, _ in settings):
                f.write(f'[{section}]\n')
                f.writelines(f'{k} = {v}\n' for (s, k), v in settings.items() if s == section)
        return
    with open(src, encoding='utf-8') as fh:
        lines = fh.readlines()
    section, headers, done = '', {}, set()
    for i, line in enumerate(lines):
        m = re.match(r'\s*\[([^\]]+)\]\s*$', line)
        if m:
            section = m.group(1).strip()
            headers[section] = i
            continue
        m = re.match(r'(\s*)([A-Za-z0-9_-]+)(\s*=\s*)(.*)$', line)
        if m and (section, m.group(2)) in settings:
            lines[i] = m.group(1) + m.group(2) + m.group(3) + settings[(section, m.group(2))] + '\n'
            done.add((section, m.group(2)))
    missing = [k for k in settings if k not in done]
    after = {}
    for section, key in missing:
        if section in headers:
            after.setdefault(headers[section], []).append(f'{key} = {settings[(section, key)]}\n')
    out = []
    for i, line in enumerate(lines):
        out.append(line)
        out += after.get(i, [])
    if out and not out[-1].endswith('\n'):
        out[-1] += '\n'
    for section in dict.fromkeys(s for s, _ in missing if s not in headers):
        out.append(f'[{section}]\n')
        out += [f'{k} = {settings[(s, k)]}\n' for s, k in missing if s == section]
    with open(dst, 'w', encoding='utf-8') as f:
        f.writelines(out)


def prepare_client(args):
    """The cameraman's own user folder, made from the player's: their keys and haks, film settings,
    and a local vault holding only the cameraman's character."""
    cdir = os.path.expanduser(args.client_dir)
    user = os.path.expanduser(args.user_dir)
    os.makedirs(cdir, exist_ok=True)
    for f in ('cdkey.ini', 'nwncdkey.ini', 'cryptographic_secret', 'nwn.ini', 'nwnplayer.ini', 'userpatch.ini'):
        if os.path.exists(os.path.join(user, f)):
            shutil.copy2(os.path.join(user, f), os.path.join(cdir, f))
    for d in ['hak', 'tlk', 'patch', 'override', 'portraits', 'music', 'ambient', 'movies'] + \
            [os.path.basename(h) for h in glob.glob(os.path.join(user, '*.hak'))]:
        src, dst = os.path.join(user, d), os.path.join(cdir, d)
        if os.path.exists(src) and not os.path.lexists(dst):
            os.symlink(src, dst)
    settings = {**CLIENT_SETTINGS, **(STILLS_SETTINGS if args.stills else {})}
    client_settings(os.path.join(user, 'settings.tml'), os.path.join(cdir, 'settings.tml'), settings)
    vault = os.path.join(cdir, 'localvault')
    shutil.rmtree(vault, ignore_errors=True)
    os.makedirs(vault)
    shutil.copy2(args.character, vault)
    return cdir


def start_client(args, game, cdir, logfile):
    binary = os.path.join(game, 'bin', 'linux-x86', 'nwmain-linux')
    env = dict(os.environ)
    if args.stills:
        # Drawn on the CPU (Mesa's llvmpipe), with no sound device.
        env.update(LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe', __GLX_VENDOR_LIBRARY_NAME='mesa',
                   SDL_AUDIODRIVER='dummy')
    return subprocess.Popen([binary, '-userdirectory', cdir, '+connect', f'127.0.0.1:{args.port}'],
                            cwd=os.path.dirname(binary), stdout=logfile, stderr=subprocess.STDOUT, env=env)


# The stills' screen (STILLS_SETTINGS): small enough to draw on a CPU, big enough to see what a bug report is about.
STILLS_SIZE = '960x540'


def start_display():
    """A virtual X display of its own for the client (stills): sets DISPLAY, returns the server."""
    n = 99
    while os.path.exists(f'/tmp/.X11-unix/X{n}') or os.path.exists(f'/tmp/.X{n}-lock'):
        n += 1
    xvfb = subprocess.Popen(['Xvfb', f':{n}', '-screen', '0', STILLS_SIZE + 'x24', '-nolisten', 'tcp'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        if os.path.exists(f'/tmp/.X11-unix/X{n}'):
            os.environ['DISPLAY'] = f':{n}'
            return xvfb
        time.sleep(0.1)
    xvfb.kill()
    die('the virtual display (Xvfb) did not start')


THUMB = ['-frames:v', '1', '-vf', 'scale=64:36:flags=area', '-f', 'rawvideo', '-pix_fmt', 'gray', '-']


def grab(path=None):
    """The screen as a 64x36 grey thumbnail (bytes) to compare frames by; with path, also saved there, from the same
    frame (one capture: on a CPU-drawn client every ffmpeg started takes from the game)."""
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'x11grab', '-video_size', STILLS_SIZE, '-i', os.environ['DISPLAY']]
    if path:
        cmd += ['-map', '0:v', '-frames:v', '1', path, '-map', '0:v']
    return subprocess.run(cmd + THUMB, capture_output=True).stdout


def loading(thumb):
    """The area loading screen: a picture and text in the middle, with black all round: the left and right 6 columns
    of the 64x36 thumbnail, and the corners (the outer 12 columns of the top and bottom 4 rows). The loading screen is
    drawn at a fixed size, so it fills more of a small screen: a tip under the picture reaches the bottom sixth at
    1280x720, and at 960x540 the area's name and the tip reach the top and bottom rows, but not their corners."""
    if len(thumb) != 64 * 36:
        return False
    rows = [thumb[r * 64:(r + 1) * 64] for r in range(36)]
    edges = [row[:12] + row[-12:] for row in rows[:4] + rows[-4:]] + [row[:6] + row[-6:] for row in rows]
    return max(max(e) for e in edges) < 8


# Seconds to wait for a still's angle to be drawn (the server holds an angle for 600).
STILL_WAIT = 540


def black(thumb):
    """A black screen (dir_load's BlackScreen while it sets the camera for a spot's stills): no picture anywhere,
    unlike a loading screen."""
    return len(thumb) == 64 * 36 and max(thumb) < 8


def differ(a, b):
    """Two thumbnails differ: a mean difference over 1.5 in 0 to 255. A turn of the camera makes 15 to 25 at 45
    degrees; animation in the scene 0.1 to 0.2."""
    return len(a) != len(b) or sum(abs(x - y) for x, y in zip(a, b)) / len(a) > 1.5


# Seconds a still's picture must hold before it is saved: a camera easing into place on a slow client changes the
# picture little from one frame to the next, but a lot over a second.
STILL_STEADY = 1.0


def wait_for_angle(limit, previous=None):
    """Wait until the client has drawn the camera's new angle, and it holds (no change over STILL_STEADY). A later
    angle: once the picture differs from the previous angle's (previous: its thumbnail). The first angle: once the
    screen has been black (dir_load blacks it out while it sets the camera, and the client does what the server says
    in order, so the picture after the black has the camera in place) and isn't any more, nor a loading screen. False
    if that doesn't happen within limit seconds."""
    start = time.time()
    seen_black = previous is not None
    held = []   # (time, thumbnail) since the new angle appeared
    while time.time() - start < limit:
        thumb = grab()
        now = time.time()
        if black(thumb):
            seen_black, held = True, []
        elif loading(thumb) or not seen_black:
            held = []
        elif held or previous is None or differ(thumb, previous):
            held.append((now, thumb))
            before = [t for t in held if t[0] <= now - STILL_STEADY]
            if before and not differ(thumb, before[-1][1]):
                return True
            if before and differ(thumb, before[-1][1]):
                held = [(now, thumb)]   # still moving: hold from here
        time.sleep(0.5)
    return False


def contact_sheet(stills, path):
    """The stills side by side, four to a row."""
    cols = min(4, len(stills))
    rows = -(-len(stills) // cols)
    inputs = sum((['-i', s] for s in stills), [])
    pad = ''.join(f'[{i}:v]scale=640:-1[s{i}];' for i in range(len(stills)))
    blank = cols * rows - len(stills)
    if blank:
        pad += ''.join(f'color=black:s=640x360:d=1[b{i}];' for i in range(blank))
    labels = ''.join(f'[s{i}]' for i in range(len(stills))) + ''.join(f'[b{i}]' for i in range(blank))
    layout = '|'.join(f'{(i % cols) * 640}_{(i // cols) * 360}' for i in range(cols * rows))
    graph = pad + labels + f'xstack=inputs={cols * rows}:layout={layout}'
    if cols * rows == 1:
        graph = '[0:v]scale=640:-1'
    subprocess.run(['ffmpeg', '-v', 'error', '-y'] + inputs + ['-filter_complex', graph, '-frames:v', '1', path],
                   check=False)


def raise_client():
    """Bring the game's window to the front: whatever is on top of it is what gets recorded (and
    what the Play click lands on). Done when it starts and again before every shot, in case
    another window came up meanwhile."""
    found = subprocess.run(['xdotool', 'search', '--onlyvisible', '--class', 'nwmain'],
                           capture_output=True, text=True).stdout.split()
    for win in found:
        subprocess.run(['xdotool', 'windowactivate', '--sync', win], capture_output=True, timeout=10)


def screen_size():
    out = subprocess.run(['xrandr', '--current'], capture_output=True, text=True).stdout
    m = re.search(r'current (\d+) x (\d+)', out)
    return f'{m.group(1)}x{m.group(2)}' if m else '1920x1080'


def encoder():
    encoders = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True).stdout
    if 'h264_nvenc' in encoders and shutil.which('nvidia-smi'):
        return ['-c:v', 'h264_nvenc', '-preset', 'p5', '-rc', 'vbr', '-cq', '19', '-b:v', '0']
    return ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20']


def record(path, size):
    """Record the screen, and the game's sound from the default output's monitor, to path."""
    monitor = shutil.which('pactl') and subprocess.run(['pactl', 'get-default-sink'], capture_output=True,
                                                       text=True).stdout.strip()
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'x11grab', '-framerate', '60', '-video_size', size,
           '-draw_mouse', '0', '-i', os.environ['DISPLAY']]
    if monitor:
        cmd += ['-f', 'pulse', '-i', monitor + '.monitor']
    cmd += encoder() + ['-pix_fmt', 'yuv420p']
    if monitor:
        cmd += ['-c:a', 'aac', '-b:a', '192k']
    return subprocess.Popen(cmd + [path], stdin=subprocess.DEVNULL)


def screen_locked():
    try:
        sessions = subprocess.run(['loginctl', 'list-sessions', '--no-legend'], capture_output=True,
                                  text=True).stdout.splitlines()
        seat = next((s.split()[0] for s in sessions if 'seat0' in s.split()), None)
        if not seat:
            return False
        out = subprocess.run(['loginctl', 'show-session', seat, '-p', 'LockedHint'], capture_output=True,
                             text=True).stdout
        return 'yes' in out
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------- commands

def find_game(arg):
    for d in ([arg] if arg else GAME_DIRS):
        d = os.path.expanduser(d)
        if os.path.exists(os.path.join(d, 'bin', 'linux-x86', 'nwmain-linux')):
            return d
    die('no NWN:EE install found; give it with --game')


def cmd_areas(args):
    cache = os.path.expanduser('~/.cache/nwn-director')
    os.makedirs(cache, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='areas-', dir=cache) as work:
        unpack(args.module, work)
        def row(item):
            resref, base = item
            are, git = gff(base + '.are'), gff(base + '.git')
            return (f"{resref:16}  {are.get('Tag', {}).get('value', ''):24}  "
                    f"{are['Width']['value']}x{are['Height']['value']}  {len(exits(git))} exits")
        with concurrent.futures.ThreadPoolExecutor(os.cpu_count() or 4) as pool:
            for line in pool.map(row, area_files(work).items()):
                print(line)


def log_in(slog, size, screenshot=None, vault_key=None, raise_window=True):
    """Get the cameraman into the game: once the client has joined the server, click Play on the character list (its
    only character is selected; the panel is a fixed size, centred, at the default interface scale), again every few
    seconds until the cameraman is in (a slow client may not have listed it yet). Dies saying why if it doesn't get
    in, with the screen saved to screenshot if given. vault_key: the public CD key whose server vault holds the
    character, if the server keeps one (with another key, Play stays greyed out)."""
    def fail(why):
        if screenshot:
            grab(screenshot)
        die(why + (f'; the screen: {os.path.basename(screenshot)}' if screenshot else ''))

    joined = slog.wait_for(r'Joined as Player', 300, fatal=False)
    if not joined:
        fail('the cameraman never got into the game: it never logged in (is the CD key in a game elsewhere? '
             '"still in use")')
    key = re.search(r'\((\w+)\) Joined as Player', joined)
    if vault_key and key and key.group(1) != vault_key:
        fail(f'the cameraman logged in with the CD key {key.group(1)}, but its character is in the vault of '
             f'{vault_key}')
    w, h = (int(n) for n in size.split('x'))
    for attempt in range(20):
        time.sleep(4 if attempt == 0 else 6)
        if raise_window:
            raise_client()
        subprocess.run(['xdotool', 'mousemove', str(w // 2 + 102), str(h // 2 + 231), 'click', '1'])
        time.sleep(0.5)
        subprocess.run(['xdotool', 'mousemove', str(w - 1), str(h - 1)])
        if slog.wait_for(r'DIRECTOR: cameraman in', 10, fatal=False):
            return
    fail('the cameraman never got into the game: it logged in but Play did nothing (the click missed?)')


def markers(slog, quiet):
    """The director's markers in the server log, as (what, rest): ROLL, STILL, CUT, END, skip or event. Stops after
    END; dies if the log goes quiet for quiet seconds."""
    for line in slog.lines(quiet):
        m = re.search(r'DIRECTOR: (ROLL|STILL|CUT|END|skip|event)(.*)', line)
        if not m:
            continue
        what, rest = m.group(1), m.group(2).strip()
        if what in ('skip', 'event'):
            log(f'{what} {rest}')
        yield what, rest
        if what == 'END':
            return


def take_stills(slog, out, db, client=None):
    """Save every still the server asks for (STILL n) as out/<shot>-NN.png, counting each in db so the server turns
    to the next angle, and a contact sheet per shot (<shot>-sheet.png). Returns the sheets."""
    name, stills, previous, sheets = '', [], None, []
    # The longest wait between markers is a slow client loading an area.
    for what, rest in markers(slog, 600):
        if what == 'ROLL':
            name, stills = re.sub(r'[^A-Za-z0-9_-]', '', rest), []
            log(f'stills of {name}')
        elif what == 'STILL':
            if client and client.poll() is not None:
                die(f'the game client stopped (exit {client.returncode}); see {out}/client.log')
            # The first still waits for the client to load the area. The server holds each angle for up to 600 s:
            # a slow client gets most of that, rather than saving the last angle again.
            if not wait_for_angle(STILL_WAIT, previous if stills else None):
                log(f'  angle {rest}: the picture did not settle; saved as it was')
            path = os.path.join(out, f'{name}-{int(rest):02d}.png')
            previous = grab(path)   # the server holds this angle until the count below goes up
            stills.append(path)
            with sqlite3.connect(db, timeout=30) as conn:
                conn.execute('UPDATE stills SET n = n + 1')
            log(f'  still {rest}')
        elif what == 'CUT' and stills:
            # -sheet: a spot's name can end in two digits (its y), as the stills do (-NN).
            sheets.append(os.path.join(out, name + '-sheet.png'))
            contact_sheet(stills, sheets[-1])
    return sheets


def film_video(slog, out, size, seconds):
    """Record every shot from its ROLL to its CUT as out/<shot>.mp4."""
    recorder = None
    try:
        # The longest wait between markers is a shot (--seconds) plus loading the next area.
        for what, rest in markers(slog, seconds + 120):
            if what == 'ROLL':
                name = re.sub(r'[^A-Za-z0-9_-]', '', rest)
                log(f'recording {name}')
                raise_client()
                recorder = record(os.path.join(out, name + '.mp4'), size)
            elif what == 'CUT' and recorder:
                recorder.send_signal(signal.SIGINT)
                recorder.wait()
                recorder = None
    finally:
        if recorder:
            recorder.send_signal(signal.SIGINT)
            recorder.wait()


def remove_server_files(image, work):
    """The server writes into its home (work/server: nwsync, the database) as the container's user, root: remove
    them from a container of the same image, or the work folder is left behind each run."""
    if os.path.isdir(os.path.join(work, 'server')):
        subprocess.run(['docker', 'run', '--rm', '-v', f'{work}:/work', '--entrypoint', 'rm', image, '-rf',
                        '/work/server'], capture_output=True)


def check_tools(args):
    tools = ['docker', 'nwn_erf', 'nwn_gff', 'nwn_script_comp', 'ffmpeg', 'xdotool']
    tools += ['Xvfb'] if args.stills else ['xrandr']
    for tool in tools:
        if not shutil.which(tool):
            die(f'{tool} not found (README.md lists what is needed)')


def cmd_film(args):
    if not args.stills:
        # A locked or sleeping screen is what would be recorded: keep the desktop awake while filming.
        if not os.environ.get('NWN_DIRECTOR_INHIBITED') and shutil.which('gnome-session-inhibit'):
            os.environ['NWN_DIRECTOR_INHIBITED'] = '1'
            os.execvp('gnome-session-inhibit', ['gnome-session-inhibit', '--inhibit', 'idle', '--reason',
                                                'nwn-director filming', sys.executable] + sys.argv)
        os.environ.setdefault('DISPLAY', ':0')
        if screen_locked():
            die('the screen is locked; unlock it and run again')
    check_tools(args)
    game = find_game(args.game)
    out = os.path.abspath(args.out or os.path.join('out', datetime.datetime.now().strftime('%Y%m%d-%H%M%S')))
    os.makedirs(out, exist_ok=True)

    # The module's copy (unpacked and packed: twice its size) on disk, not in /tmp, which is in memory on some systems.
    cache = os.path.expanduser('~/.cache/nwn-director')
    os.makedirs(cache, exist_ok=True)
    work = tempfile.mkdtemp(prefix='work-', dir=cache)
    # The neverwinter.nim tools look for an NWN user folder; on a server or CI runner there may be
    # none where they look.
    user = os.path.expanduser(args.user_dir)
    os.environ.setdefault('NWN_HOME', user if os.path.isdir(user) else work)
    client = slog = xvfb = None
    try:
        home = prepare(args, game, work)
        log('starting the server')
        start_server(args, home)
        slog = ServerLog()
        slog.wait_for(r'DIRECTOR: module loaded', 600)

        if args.stills:
            xvfb = start_display()
        log('starting the game client' + (' (drawing on the CPU, in a virtual display)' if args.stills else ''))
        cdir = prepare_client(args)
        client = start_client(args, game, cdir, open(os.path.join(out, 'client.log'), 'w'))
        size = STILLS_SIZE if args.stills else screen_size()
        log_in(slog, size, screenshot=os.path.join(out, 'no-cameraman.png') if args.stills else None,
               raise_window=not args.stills)
        if args.stills:
            take_stills(slog, out, os.path.join(home, 'database', 'director.sqlite3'), client)
        else:
            film_video(slog, out, size, args.seconds)
    finally:
        if client:
            # SIGKILL: on a normal quit the client crashes tearing down the cameraman's cutscene
            # invisibility effect and leaves a crash dialog on the desktop.
            client.kill()
        if slog:
            slog.close()
        if xvfb:
            xvfb.kill()
        if not args.keep_server:
            subprocess.run(['docker', 'rm', '-f', CONTAINER], capture_output=True)
            remove_server_files(args.image, work)
        shutil.rmtree(work, ignore_errors=True)
    log(f'done: {out}')


def shot_specs(args):
    """The shots asked for, as (kind, area, where, event) in filming order; dies on anything malformed."""
    specs = [('walk', a.strip(), None, None) for a in (args.areas or '').split(',') if a.strip()]
    for spot in args.spot:
        area, sep, where_ = spot.partition('@')
        if not sep or not area or not where_:
            die(f'--spot {spot}: expected AREA@X,Y or AREA@TAG')
        specs.append(('spot', area.strip(), where_.strip(), None))
    for event in args.event:
        place, sep, code = event.partition('=')
        area, sep2, where_ = place.partition('@')
        if not sep or not sep2 or not area or not where_ or not code.strip():
            die(f'--event {event}: expected AREA@X,Y=CODE or AREA@TAG=CODE')
        specs.append(('spot', area.strip(), where_.strip(), code.strip()))
    if not specs:
        die('nothing to film: give --areas and/or --spot')
    if args.stills < 0:
        die('--stills needs a number of stills')
    if args.stills and (args.areas or args.event):
        die('--stills takes spots only (--spot), not --areas or --event')
    return specs


def main():
    p = argparse.ArgumentParser(description='Film walkthroughs of NWN:EE module areas.')
    sub = p.add_subparsers(dest='command', required=True)

    a = sub.add_parser('areas', help="list a module's areas")
    a.add_argument('module')
    a.set_defaults(func=cmd_areas)

    f = sub.add_parser('film', help='film walkthroughs of areas')
    f.add_argument('module', help='the .mod to film (it is copied, never changed)')
    f.add_argument('--areas', help='walkthroughs: areas (resrefs or tags), comma-separated, in filming order')
    f.add_argument('--spot', action='append', default=[], metavar='AREA@X,Y[/FACING]|AREA@TAG',
                   help='circle one place: a point in an area, or an object by tag; /FACING starts the '
                        'circle looking that way, as a bug report\'s location gives it (repeatable)')
    f.add_argument('--event', action='append', default=[], metavar='AREA@X,Y=CODE|AREA@TAG=CODE',
                   help='a spot where CODE runs a few seconds in: a script of the module, or NWScript '
                        'statements (checked before filming), with OBJECT_SELF the tagged object (or the '
                        'invisible walker at the point) (repeatable)')
    f.add_argument('--character', required=True, help="the cameraman's character (.bic)")
    f.add_argument('--stills', type=int, default=0, metavar='N',
                   help='spots as N stills (one every 360/N degrees) and a contact sheet instead of a video, '
                        'drawn on the CPU in a virtual display: no GPU, and your desktop is left alone')
    f.add_argument('--seconds', type=float, default=45, help='seconds to film each shot at most (45)')
    f.add_argument('--run', action='store_true', help='the walker runs instead of walking')
    f.add_argument('--out', help='folder for the videos (out/<date-time>)')
    f.add_argument('--game', help='NWN:EE install folder (found by itself in the usual places)')
    f.add_argument('--user-dir', default=USER_DIR, help=f'your NWN user folder ({USER_DIR})')
    f.add_argument('--hak-dir', help='the haks the module needs (your user folder\'s hak)')
    f.add_argument('--tlk-dir', help='the custom TLK the module needs (your user folder\'s tlk)')
    f.add_argument('--client-dir', default='~/.cache/nwn-director/client',
                   help="the cameraman client's own user folder (~/.cache/nwn-director/client)")
    f.add_argument('--port', type=int, default=5121, help='server port on 127.0.0.1 (5121)')
    f.add_argument('--image', default=SERVER_IMAGE, help=f'server Docker image ({SERVER_IMAGE})')
    f.add_argument('--keep-server', action='store_true', help='leave the server running afterwards')
    f.add_argument('--script-log', action='store_true',
                   help='log every script the server runs (with --keep-server: docker logs nwn-director)')
    f.set_defaults(func=cmd_film)

    args = p.parse_args()
    if args.command == 'film':
        user = os.path.expanduser(args.user_dir)
        args.hak_dir = os.path.expanduser(args.hak_dir or os.path.join(user, 'hak'))
        args.tlk_dir = os.path.expanduser(args.tlk_dir or os.path.join(user, 'tlk'))
        if not os.path.exists(args.character):
            die(f'no character at {args.character}')
        args.shot_specs = shot_specs(args)
    args.func(args)


if __name__ == '__main__':
    main()
