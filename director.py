#!/usr/bin/env python3
"""nwn-director: film the areas of a Neverwinter Nights: Enhanced Edition module.

    director.py film MODULE.mod [--areas AREA[,AREA...]] [--spot AREA@X,Y|AREA@TAG ...]
                                [--event AREA@X,Y=CODE|AREA@TAG=CODE ...] --character CHAR.bic [options]
    director.py areas MODULE.mod

`film` copies the module with nwn-director's script in it, runs the copy on a local server
(Docker), logs your game client in as the cameraman and records every shot to OUT/<name>.mp4:
a walkthrough of each area in --areas (<area>.mp4), a slow circle around each --spot
(<area>-<x>-<y>.mp4 or <area>-<tag>.mp4), and the same for each --event, with its CODE run a
few seconds in (<area>-<x>-<y>-event.mp4 or <area>-<tag>-event.mp4).
`areas` lists the module's areas (resref, tag, size, exits) to choose from.

See README.md for requirements and how it works.
"""
import argparse
import datetime
import glob
import json
import math
import os
import queue
import re
import shutil
import signal
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


def encounters(git):
    """The middle of every encounter trigger: the cameraman steps into each before filming, so the
    area's monsters and wildlife spawn."""
    out = []
    for e in git.get('Encounter List', {}).get('value', []):
        geo = e.get('Geometry', {}).get('value', [])
        if geo:
            out.append([round(e['XPosition']['value'] + sum(p['X']['value'] for p in geo) / len(geo), 2),
                        round(e['YPosition']['value'] + sum(p['Y']['value'] for p in geo) / len(geo), 2)])
    return out


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
    """A shot circling one place in an area: where_ is "X,Y" or an object's tag. An event (a script
    name, or NWScript statements) runs a few seconds in."""
    shot = {'area': area, 'kind': 'spot', 'encounters': encounters(git)}
    m = re.fullmatch(r'\s*(-?[0-9.]+)\s*,\s*(-?[0-9.]+)\s*', where_)
    if m:
        shot['at'] = [float(m.group(1)), float(m.group(2))]
        shot['name'] = f'{area}-{round(float(m.group(1)))}-{round(float(m.group(2)))}'
    else:
        shot['tag'] = where_
        shot['name'] = f'{area}-{re.sub(r"[^A-Za-z0-9_-]", "", where_).lower()}'
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
    shots = []
    for spec in args.shot_specs:
        kind, area, where_, event = spec
        if area not in areas:
            die(f'no area {area} in the module (director.py areas {args.module} lists them)')
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
    if args.script_log:
        # Every script run goes to the server log (docker logs nwn-director), for finding what a
        # module's own scripts do to the walker.
        with open(os.path.join(home, 'settings.tml'), 'w') as f:
            f.write('[nwscript]\n\t[nwscript.log]\n\t\t[nwscript.log.script-execution]\n\t\t\tenabled = true\n')
    mod = os.path.join(home, 'modules', 'director.mod')
    log(f'packing the copy ({len(shots)} shots)')
    run(['nwn_erf', '--quiet', '-c', '-e', 'MOD', '-f', mod] + sorted(glob.glob(os.path.join(unpacked, '*'))))
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
        """The next line, or '' if none came within timeout seconds."""
        try:
            line = self.queue.get(timeout=timeout)
        except queue.Empty:
            return ''
        if line is None:
            die('the server stopped; see: docker logs nwn-director')
        return line

    def wait_for(self, pattern, timeout):
        end = time.time() + timeout
        while time.time() < end:
            line = self.next(end - time.time())
            if line and re.search(pattern, line):
                return line
        die(f'timed out waiting for "{pattern}" in the server log (docker logs nwn-director)')

    def lines(self, quiet_timeout):
        """Lines until none comes for quiet_timeout seconds."""
        while True:
            line = self.next(quiet_timeout)
            if not line:
                die(f'the server log went quiet for {quiet_timeout} s (docker logs nwn-director)')
            yield line

    def close(self):
        self.proc.kill()


# ---------------------------------------------------------------- client

def edit_settings(src, dst):
    section = ''
    out = []
    for line in open(src, encoding='utf-8'):
        m = re.match(r'\s*\[([^\]]+)\]\s*$', line)
        if m:
            section = m.group(1)
        else:
            m = re.match(r'(\s*)([A-Za-z0-9_-]+)(\s*=\s*)(.*)$', line)
            if m and (section, m.group(2)) in CLIENT_SETTINGS:
                line = m.group(1) + m.group(2) + m.group(3) + CLIENT_SETTINGS[(section, m.group(2))] + '\n'
        out.append(line)
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
    if os.path.exists(os.path.join(user, 'settings.tml')):
        edit_settings(os.path.join(user, 'settings.tml'), os.path.join(cdir, 'settings.tml'))
    vault = os.path.join(cdir, 'localvault')
    shutil.rmtree(vault, ignore_errors=True)
    os.makedirs(vault)
    shutil.copy2(args.character, vault)
    return cdir


def start_client(args, game, cdir, logfile):
    binary = os.path.join(game, 'bin', 'linux-x86', 'nwmain-linux')
    return subprocess.Popen([binary, '-userdirectory', cdir, '+connect', f'127.0.0.1:{args.port}'],
                            cwd=os.path.dirname(binary), stdout=logfile, stderr=subprocess.STDOUT)


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
    with tempfile.TemporaryDirectory(prefix='nwn-director-') as work:
        unpack(args.module, work)
        for resref, base in area_files(work).items():
            are, git = gff(base + '.are'), gff(base + '.git')
            print(f"{resref:16}  {are.get('Tag', {}).get('value', ''):24}  "
                  f"{are['Width']['value']}x{are['Height']['value']}  {len(exits(git))} exits")


def cmd_film(args):
    # A locked or sleeping screen is what would be recorded: keep the desktop awake while filming.
    if not os.environ.get('NWN_DIRECTOR_INHIBITED') and shutil.which('gnome-session-inhibit'):
        os.environ['NWN_DIRECTOR_INHIBITED'] = '1'
        os.execvp('gnome-session-inhibit', ['gnome-session-inhibit', '--inhibit', 'idle', '--reason',
                                            'nwn-director filming', sys.executable] + sys.argv)
    os.environ.setdefault('DISPLAY', ':0')
    if screen_locked():
        die('the screen is locked; unlock it and run again')
    for tool in ('docker', 'nwn_erf', 'nwn_gff', 'nwn_script_comp', 'ffmpeg', 'xdotool', 'xrandr'):
        if not shutil.which(tool):
            die(f'{tool} not found (README.md lists what is needed)')
    game = find_game(args.game)
    out = os.path.abspath(args.out or os.path.join('out', datetime.datetime.now().strftime('%Y%m%d-%H%M%S')))
    os.makedirs(out, exist_ok=True)

    work = tempfile.mkdtemp(prefix='nwn-director-')
    client = recorder = slog = None
    try:
        home = prepare(args, game, work)
        log('starting the server')
        start_server(args, home)
        slog = ServerLog()
        slog.wait_for(r'DIRECTOR: module loaded', 600)

        log('starting the game client')
        cdir = prepare_client(args)
        client = start_client(args, game, cdir, open(os.path.join(out, 'client.log'), 'w'))
        slog.wait_for(r'Joined as Player', 300)
        time.sleep(4)
        # Character list: the only character is selected. Click Play (the panel is a fixed size,
        # centred, at the default interface scale), then park the pointer in a corner.
        size = screen_size()
        w, h = (int(n) for n in size.split('x'))
        subprocess.run(['xdotool', 'mousemove', str(w // 2 + 102), str(h // 2 + 231), 'click', '1'])
        time.sleep(0.5)
        subprocess.run(['xdotool', 'mousemove', str(w - 1), str(h - 1)])
        slog.wait_for(r'DIRECTOR: cameraman in', 120)

        # Markers come at least every area (the longest wait is --seconds plus loading).
        for line in slog.lines(args.seconds + 120):
            m = re.search(r'DIRECTOR: (ROLL|CUT|END|skip|event)(.*)', line)
            if not m:
                continue
            what, rest = m.group(1), m.group(2).strip()
            if what == 'ROLL':
                name = re.sub(r'[^A-Za-z0-9_-]', '', rest)
                log(f'recording {name}')
                recorder = record(os.path.join(out, name + '.mp4'), size)
            elif what == 'CUT' and recorder:
                recorder.send_signal(signal.SIGINT)
                recorder.wait()
                recorder = None
            elif what in ('skip', 'event'):
                log(f'{what} {rest}')
            elif what == 'END':
                break
    finally:
        if recorder:
            recorder.send_signal(signal.SIGINT)
            recorder.wait()
        if client:
            # SIGKILL: on a normal quit the client crashes tearing down the cameraman's cutscene
            # invisibility effect and leaves a crash dialog on the desktop.
            client.kill()
        if slog:
            slog.close()
        if not args.keep_server:
            subprocess.run(['docker', 'rm', '-f', CONTAINER], capture_output=True)
        shutil.rmtree(work, ignore_errors=True)
    log(f'done: {out}')


def main():
    p = argparse.ArgumentParser(description='Film walkthroughs of NWN:EE module areas.')
    sub = p.add_subparsers(dest='command', required=True)

    a = sub.add_parser('areas', help="list a module's areas")
    a.add_argument('module')
    a.set_defaults(func=cmd_areas)

    f = sub.add_parser('film', help='film walkthroughs of areas')
    f.add_argument('module', help='the .mod to film (it is copied, never changed)')
    f.add_argument('--areas', help='walkthroughs: area resrefs, comma-separated, in filming order')
    f.add_argument('--spot', action='append', default=[], metavar='AREA@X,Y|AREA@TAG',
                   help='circle one place: a point in an area, or an object by tag (repeatable)')
    f.add_argument('--event', action='append', default=[], metavar='AREA@X,Y=CODE|AREA@TAG=CODE',
                   help='a spot where CODE runs a few seconds in: a script of the module, or NWScript '
                        'statements (checked before filming), with OBJECT_SELF the tagged object (or the '
                        'invisible walker at the point) (repeatable)')
    f.add_argument('--character', required=True, help="the cameraman's character (.bic)")
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
        args.shot_specs = [('walk', a.strip().lower(), None, None)
                           for a in (args.areas or '').split(',') if a.strip()]
        for spot in args.spot:
            area, sep, where_ = spot.partition('@')
            if not sep or not area or not where_:
                die(f'--spot {spot}: expected AREA@X,Y or AREA@TAG')
            args.shot_specs.append(('spot', area.strip().lower(), where_.strip(), None))
        for event in args.event:
            place, sep, code = event.partition('=')
            area, sep2, where_ = place.partition('@')
            if not sep or not sep2 or not area or not where_ or not code.strip():
                die(f'--event {event}: expected AREA@X,Y=CODE or AREA@TAG=CODE')
            args.shot_specs.append(('spot', area.strip().lower(), where_.strip(), code.strip()))
        if not args.shot_specs:
            die('nothing to film: give --areas and/or --spot')
    args.func(args)


if __name__ == '__main__':
    main()
