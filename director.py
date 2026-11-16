#!/usr/bin/env python3
"""nwn-director: film walkthroughs of the areas of a Neverwinter Nights: Enhanced Edition module.

    director.py film MODULE.mod --areas AREA[,AREA...] --character CHAR.bic [options]
    director.py areas MODULE.mod

`film` copies the module with nwn-director's script in it, runs the copy on a local server
(Docker), logs your game client in as the cameraman and records every area to OUT/<area>.mp4.
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


def route(git):
    """The walker's stops in an area, nearest first from its first exit: every exit, placed
    creature and encounter spawn point; and the middle of every encounter (the cameraman steps
    into each before filming so they spawn)."""
    pts = exits(git)
    if not pts:
        return {'stops': [], 'encounters': []}
    for c in git.get('Creature List', {}).get('value', []):
        pts.append((c['XPosition']['value'], c['YPosition']['value']))
    encounters = []
    for e in git.get('Encounter List', {}).get('value', []):
        for sp in e.get('SpawnPointList', {}).get('value', []):
            pts.append((sp['X']['value'], sp['Y']['value']))
        geo = e.get('Geometry', {}).get('value', [])
        if geo:
            encounters.append([round(e['XPosition']['value'] + sum(p['X']['value'] for p in geo) / len(geo), 2),
                               round(e['YPosition']['value'] + sum(p['Y']['value'] for p in geo) / len(geo), 2)])
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
    return {'stops': [[round(v, 2) for v in p] for p in stops], 'encounters': encounters}


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


def prepare(args, game, work):
    """Copy the module with dir_load as its OnModuleLoad and the walkthroughs in module variables.
    Returns the copy's path."""
    unpacked = os.path.join(work, 'module')
    log(f'unpacking {args.module}')
    unpack(args.module, unpacked)
    if os.path.exists(os.path.join(unpacked, 'dir_load.ncs')):
        die('the module already has a script named dir_load')

    areas = area_files(unpacked)
    wanted = [a.strip().lower() for a in args.areas.split(',') if a.strip()]
    missing = [a for a in wanted if a not in areas]
    if missing:
        die(f'not in the module: {", ".join(missing)} (director.py areas {args.module} lists them)')
    routes = {a: route(gff(areas[a] + '.git')) for a in wanted}
    for a in wanted:
        if len(routes[a]['stops']) < 2:
            log(f'{a} has no exits to walk between; it will be skipped')

    ifo_path = os.path.join(unpacked, 'module.ifo')
    ifo = gff(ifo_path)
    haks = [h['Mod_Hak']['value'] for h in ifo.get('Mod_HakList', {}).get('value', [])]
    missing = [h for h in haks if not os.path.exists(os.path.join(args.hak_dir, h + '.hak'))]
    if missing:
        die(f'haks the module needs are not in {args.hak_dir}: {", ".join(missing)} (--hak-dir)')
    set_var(ifo, 'DIRECTOR_ONLOAD', 3, ifo.get('Mod_OnModLoad', {}).get('value', ''))
    set_var(ifo, 'DIRECTOR_AREAS', 3, ','.join(wanted))
    set_var(ifo, 'DIRECTOR_ROUTES', 3, json.dumps(routes, separators=(',', ':')))
    set_var(ifo, 'DIRECTOR_SECONDS', 2, float(args.seconds))
    set_var(ifo, 'DIRECTOR_RUN', 1, 1 if args.run else 0)
    ifo['Mod_OnModLoad'] = {'type': 'resref', 'value': 'dir_load'}
    with open(ifo_path + '.json', 'w') as f:
        json.dump(ifo, f)
    run(['nwn_gff', '--quiet', '-i', ifo_path + '.json', '-l', 'json', '-o', ifo_path, '-k', 'gff'])
    os.remove(ifo_path + '.json')

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
    log(f'packing the copy ({len(wanted)} areas)')
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
    run(cmd, capture_output=True)


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
            m = re.search(r'DIRECTOR: (ROLL|CUT|END|skip)(.*)', line)
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
            elif what == 'skip':
                log('skip ' + rest)
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
    f.add_argument('--areas', required=True, help='area resrefs, comma-separated, in filming order')
    f.add_argument('--character', required=True, help="the cameraman's character (.bic)")
    f.add_argument('--seconds', type=float, default=45, help='seconds to film each area at most (45)')
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
    args.func(args)


if __name__ == '__main__':
    main()
