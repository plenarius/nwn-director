"""GUI shots: pictures of the game client's own (non-NUI) windows in each of their states.

    director.py gui [MODULE.mod | HAK,HAK...] --character CHAR.bic [--scale 1,1.5] [--out DIR]
    director.py gui-compare A_DIR B_DIR [--out DIR]

The old GUI is drawn by the client alone, from the interface textures and fonts it finds (the base game's, or
a hak's that replaces them), so only a real client can show it. `gui` makes a small module (two rooms, no
scripts but nwn-director's) that loads the given haks, logs the client in, drawing on the CPU in a virtual
display of its own, and works through a shot list (gui_shots.json): each shot stages something with NWScript
on the server, opens a window with the keyboard or mouse, and saves the screen once it has settled. Out come
<window>-<state>.png, a contact sheet and manifest.json. `gui-compare` puts two such runs side by side with a
difference score for each shot, so a reviewer looks only at what changed.
"""
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time

import director as d

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS_FILE = os.path.join(HERE, 'gui_shots.json')

# The client's screen at UI scale 1, and at other scales the same layout made bigger (1920x1080 at 1.5), so a
# shot's positions (logical pixels, see Screen) land on the same control at every scale.
BASE_SIZE = (1280, 720)

# The two rooms of the generated module: base game interior tiles (tic01), so they draw fast on a CPU.
ROOM_TILES = [(82, 2), (82, 3), (83, 2), (83, 0), (83, 2), (83, 0), (82, 1), (82, 0)]
ROOM_SIZE = (2, 4)
# loadscreens.2da row of the area change's loading screen: a fixed one, so every run shows the same picture.
LOAD_SCREEN = 2

# Settings for the GUI client on top of film's and stills': nothing over the interface but the interface.
GUI_SETTINGS = {
    ('ui', 'floating-text-feedback'): 'false',
    ('ui', 'mouseover-feedback'): 'false',
    ('ui', 'display-loadscreen-hints-in-chatlog'): 'false',
    ('ui', 'tooltip-delay'): '100000',
    ('ui', 'unconstrain-scale'): 'false',
    ('graphics.framelimiter.foreground', 'target-fps'): '20',
    ('graphics.framelimiter.background', 'target-fps'): '20',
}


def field(kind, value):
    return {'type': kind, 'value': value}


def room(resref, name, size=ROOM_SIZE):
    """A room of ROOM_TILES (repeated to fill size) as .are and .git (nwn_gff JSON)."""
    pattern = ROOM_TILES * -(-size[0] * size[1] // len(ROOM_TILES))
    tiles = [{'__struct_id': 1, 'Tile_ID': field('int', t), 'Tile_Orientation': field('int', o),
              'Tile_Height': field('int', 0), 'Tile_MainLight1': field('byte', 0),
              'Tile_MainLight2': field('byte', 0), 'Tile_SrcLight1': field('byte', 8),
              'Tile_SrcLight2': field('byte', 0), 'Tile_AnimLoop1': field('byte', 1),
              'Tile_AnimLoop2': field('byte', 1), 'Tile_AnimLoop3': field('byte', 1)}
             for t, o in pattern[:size[0] * size[1]]]
    are = {'__data_type': 'ARE ', 'Version': field('dword', 18), 'ResRef': field('resref', resref),
           'Tag': field('cexostring', resref), 'Name': field('cexolocstring', {'0': name}),
           'Comments': field('cexostring', ''), 'Creator_ID': field('int', -1), 'ID': field('int', -1),
           'Width': field('int', size[0]), 'Height': field('int', size[1]),
           'Tileset': field('resref', 'tic01'), 'Tile_List': field('list', tiles),
           'Flags': field('dword', 1), 'LightingScheme': field('byte', 17), 'IsNight': field('byte', 0),
           'DayNightCycle': field('byte', 0), 'LoadScreenID': field('word', LOAD_SCREEN),
           'NoRest': field('byte', 0), 'PlayerVsPlayer': field('byte', 0), 'ShadowOpacity': field('byte', 60),
           'ChanceLightning': field('int', 0), 'ChanceRain': field('int', 0), 'ChanceSnow': field('int', 0),
           'WindPower': field('int', 0), 'ModListenCheck': field('int', 0), 'ModSpotCheck': field('int', 0),
           'Expansion_List': field('list', []),
           'OnEnter': field('resref', ''), 'OnExit': field('resref', ''), 'OnHeartbeat': field('resref', ''),
           'OnUserDefined': field('resref', '')}
    for light in ('Sun', 'Moon'):
        are.update({f'{light}AmbientColor': field('dword', 0x404040), f'{light}DiffuseColor': field('dword', 0x808080),
                    f'{light}FogAmount': field('byte', 0), f'{light}FogColor': field('dword', 0),
                    f'{light}Shadows': field('byte', 0)})
    props = {'__struct_id': 100, 'AmbientSndDay': field('int', 0), 'AmbientSndDayVol': field('int', 0),
             'AmbientSndNight': field('int', 0), 'AmbientSndNitVol': field('int', 0), 'EnvAudio': field('int', 0),
             'MusicBattle': field('int', 0), 'MusicDay': field('int', 0), 'MusicDelay': field('int', 0),
             'MusicNight': field('int', 0)}
    git = {'__data_type': 'GIT ', 'AreaProperties': {'__struct_id': 100, 'type': 'struct', 'value': props}}
    for name in ('Creature List', 'Door List', 'Encounter List', 'List', 'Placeable List', 'SoundList',
                 'StoreList', 'TriggerList', 'WaypointList'):
        git[name] = field('list', [])
    return are, git


def module_ifo(haks, tlk, areas):
    """module.ifo of the generated module: starting in the first area, with nwn-director's dir_gui as its load
    and client-enter scripts and every other event empty."""
    ifo = {'__data_type': 'IFO ', 'Expansion_Pack': field('word', 3), 'Mod_Creator_ID': field('int', 2),
           'Mod_Version': field('dword', 3), 'Mod_Name': field('cexolocstring', {'0': 'nwn-director GUI'}),
           'Mod_Tag': field('cexostring', 'DIRECTOR_GUI'), 'Mod_Description': field('cexolocstring', {}),
           'Mod_IsSaveGame': field('byte', 0), 'Mod_MinGameVer': field('cexostring', '1.69'),
           'Mod_Entry_Area': field('resref', areas[0]), 'Mod_Entry_X': field('float', 10.0),
           'Mod_Entry_Y': field('float', 15.0), 'Mod_Entry_Z': field('float', 0.0),
           'Mod_Entry_Dir_X': field('float', 0.0), 'Mod_Entry_Dir_Y': field('float', -1.0),
           'Mod_DawnHour': field('byte', 6), 'Mod_DuskHour': field('byte', 18), 'Mod_MinPerHour': field('byte', 2),
           'Mod_StartDay': field('byte', 1), 'Mod_StartHour': field('byte', 12), 'Mod_StartMonth': field('byte', 6),
           'Mod_StartYear': field('dword', 1372), 'Mod_XPScale': field('byte', 10),
           'Mod_StartMovie': field('resref', ''), 'Mod_Hak': field('cexostring', ''),
           'Mod_CustomTlk': field('cexostring', tlk or ''),
           'Mod_Area_list': field('list', [{'__struct_id': 6, 'Area_Name': field('resref', a)} for a in areas]),
           'Mod_HakList': field('list', [{'__struct_id': 8, 'Mod_Hak': field('cexostring', h)} for h in haks]),
           'Mod_Expan_List': field('list', []), 'Mod_CutSceneList': field('list', []),
           'Mod_GVar_List': field('list', [])}
    for event in ('OnAcquirItem', 'OnActvtItem', 'OnClientLeav', 'OnHeartbeat', 'OnModStart', 'OnPlrDeath',
                  'OnPlrDying', 'OnPlrLvlUp', 'OnPlrRest', 'OnSpawnBtnDn', 'OnUnAqreItem', 'OnUsrDefined',
                  'OnPlrEqItm', 'OnPlrUnEqItm', 'OnCutsnAbort', 'OnPlrChat'):
        ifo['Mod_' + event] = field('resref', '')
    ifo['Mod_OnModLoad'] = field('resref', 'dir_gui')
    ifo['Mod_OnClientEntr'] = field('resref', 'dir_gui')
    return ifo


GUI_AREAS = ('dir_gui_a', 'dir_gui_b')
# The second room is big so that loading it takes long enough to catch its loading screen's bar more than once.
BIG_ROOM = (24, 24)


def make_module(game, work, haks, tlk):
    """The GUI module: two rooms, dir_gui, the given haks (names, no .hak) and custom TLK. Returns its path."""
    src = os.path.join(work, 'module')
    os.makedirs(src, exist_ok=True)
    for i, resref in enumerate(GUI_AREAS):
        are, git = room(resref, f'Room {i + 1}', BIG_ROOM if i else ROOM_SIZE)
        d.write_gff(are, os.path.join(src, resref + '.are'))
        d.write_gff(git, os.path.join(src, resref + '.git'))
    d.write_gff(module_ifo(haks, tlk, list(GUI_AREAS)), os.path.join(src, 'module.ifo'))
    d.run(['nwn_script_comp', '--quiet', '--root', game, '-o', os.path.join(src, 'dir_gui.ncs'),
           os.path.join(HERE, 'nss', 'dir_gui.nss')])
    home = os.path.join(work, 'server')
    os.makedirs(os.path.join(home, 'modules'), exist_ok=True)
    steps_db(os.path.join(home, 'database'))
    d.run(['nwn_erf', '--quiet', '-c', '-e', 'MOD', '-f', os.path.join(home, 'modules', 'director.mod'), src])
    return home


def steps_db(db_dir):
    """The steps table dir_gui reads (campaign database "director"), empty, writable by the server's user."""
    os.makedirs(db_dir, exist_ok=True)
    os.chmod(db_dir, 0o777)
    path = os.path.join(db_dir, 'director.sqlite3')
    if os.path.exists(path):
        os.remove(path)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE steps (n INTEGER PRIMARY KEY, code TEXT)')
    os.chmod(path, 0o666)
    return path


# ---------------------------------------------------------------- the screen


class Screen:
    """The client's virtual display: size, UI scale, and positions in logical pixels (BASE_SIZE at UI scale 1)
    from an anchor (c, t, b, l, r, tl, tr, bl, br: centre, edges and corners), which is how the game lays out
    its windows: each is centred or pinned to an edge or corner, and scales with the UI."""

    def __init__(self, scale):
        self.scale = scale
        self.w, self.h = (round(v * scale) for v in BASE_SIZE)

    @property
    def size(self):
        return f'{self.w}x{self.h}'

    def at(self, x, y, anchor='c'):
        ax = {'l': 0, 'r': self.w}.get(next((c for c in anchor if c in 'lr'), ''), self.w / 2)
        ay = {'t': 0, 'b': self.h}.get(next((c for c in anchor if c in 'tb'), ''), self.h / 2)
        return round(ax + x * self.scale), round(ay + y * self.scale)

    def grab(self, path=None):
        """The screen as a 64x36 grey thumbnail (bytes) to compare frames by; with path, also saved there, from
        the same frame."""
        cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'x11grab', '-draw_mouse', '0', '-video_size', self.size,
               '-i', os.environ['DISPLAY']]
        if path:
            cmd += ['-map', '0:v', '-frames:v', '1', path, '-map', '0:v']
        return subprocess.run(cmd + d.THUMB, capture_output=True).stdout


def mean_diff(a, b):
    """Mean difference of two thumbnails, 0 to 255."""
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


# A window opening changes the thumbnail by this much on average (a small window: a few units); the cursor
# blinking in a text box or a torch flickering, under 0.3.
CHANGED = 0.4
STEADY = 0.15
# Seconds a picture must hold to count as settled.
HOLD = 1.0


def settle(screen, before=None, limit=30.0, steady=STEADY):
    """Wait until the screen has changed from before (if given) and then holds still for HOLD seconds, polling
    the screen; steady: how still is still (an animated background moves more). Returns (changed, thumbnail): changed is False if it never changed from before within limit (the
    window didn't open), or never held still."""
    start = time.time()
    held, changed = [], before is None
    while time.time() - start < limit:
        thumb = screen.grab()
        now = time.time()
        if not changed and mean_diff(thumb, before) > CHANGED:
            changed = True
        if changed:
            held.append((now, thumb))
            old = [t for t in held if t[0] <= now - HOLD]
            if old:
                settle.noise = mean_diff(thumb, old[-1][1])
                if os.environ.get('DIRECTOR_DEBUG'):
                    d.log(f'  settle: {settle.noise:.3f}')
                if settle.noise <= steady:
                    return True, thumb
                held = [(now, thumb)]
        time.sleep(0.2)
    return False, screen.grab()


settle.noise = 0.0


# ---------------------------------------------------------------- the shot list


STEP_KINDS = ('nss', 'key', 'type', 'move', 'click', 'rclick', 'dclick', 'press', 'release', 'scroll', 'wait',
              'capture', 'series', 'loading')


def load_shots(path):
    """The shot list (gui_shots.json): {"shots": [{"window", "steps": [...], "close": [...]}]}. Checks every step
    is one nwn-director knows; dies naming the first that isn't."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    shots = data.get('shots')
    if not isinstance(shots, list) or not shots:
        d.die(f'{path}: no "shots" list')
    names = set()
    for i, shot in enumerate(shots):
        window = shot.get('window')
        if not window or not re.fullmatch(r'[a-z0-9_-]+', window):
            d.die(f'{path}: shot {i}: "window" must be lower-case letters, digits, - or _')
        if shot.get('fresh') and shot.get('phase', 'game') != 'pregame':
            d.die(f'{path}: {window}: "fresh" is for pregame shots only (a connected client can\'t be restarted '
                  'at once: the server holds its CD key)')
        for step in shot.get('steps', []) + shot.get('close', []):
            kind = step_kind(step)
            if not kind:
                d.die(f'{path}: {window}: unknown step {json.dumps(step)}; steps are {", ".join(STEP_KINDS)}')
            if kind in ('capture', 'series', 'loading'):
                for name in capture_names(window, step):
                    if name in names:
                        d.die(f'{path}: {name} is captured twice')
                    names.add(name)
    return shots


def step_kind(step):
    kinds = [k for k in STEP_KINDS if k in step] if isinstance(step, dict) else []
    return kinds[0] if len(kinds) == 1 else None


def capture_names(window, step):
    """The picture names a capture step makes: <window>-<state>, or for a series <window>-<state>-NN."""
    if 'capture' in step:
        return [f'{window}-{step["capture"]}']
    if 'series' in step:
        return [f'{window}-{step["series"]}-{i:02d}' for i in range(int(step.get('count', 4)))]
    return [f'{window}-{step["loading"]}-{i:02d}' for i in range(int(step.get('count', 4)))]


def sheet_names(shots):
    return [n for shot in shots for step in shot.get('steps', []) if step_kind(step) in ('capture', 'series', 'loading')
            for n in capture_names(shot['window'], step)]


# ---------------------------------------------------------------- running it


class Runner:
    """Works through the shot list on a logged-in client: staging steps go to the server (the steps table),
    input to the client's window (xdotool), pictures to out."""

    def __init__(self, screen, slog, db, out):
        self.screen, self.slog, self.db, self.out = screen, slog, db, out
        self.n = 0
        self.manifest = []

    def nss(self, code, timeout=30):
        """Run NWScript on the player (dir_gui) and wait for it to have run. Returns '' or the compiler's error."""
        self.n += 1
        with sqlite3.connect(self.db, timeout=30) as conn:
            conn.execute('INSERT INTO steps VALUES (?, ?)', (self.n, code))
        line = self.slog.wait_for(rf'DIRECTOR: (DONE|FAIL) {self.n}\b', timeout, fatal=False)
        if line is None:
            return 'the server did not run it'
        m = re.search(rf'FAIL {self.n} (.*)', line)
        return m.group(1).strip() if m else ''

    def xdo(self, *cmd):
        subprocess.run(['xdotool'] + [str(c) for c in cmd], capture_output=True, timeout=20)

    def focus(self):
        """Give the game window the keyboard: Xvfb has no window manager, so nothing else does."""
        self.xdo('search', '--name', 'Neverwinter Nights', 'windowfocus')

    def mouse(self, step, key):
        pos = step[key]
        x, y = self.screen.at(pos[0], pos[1], step.get('anchor', 'c'))
        self.xdo('mousemove', x, y)
        return x, y

    def park(self):
        """The mouse out of the way, over the chat log, where the game draws no hover (at a screen edge it would
        scroll the camera)."""
        self.xdo('mousemove', *self.screen.at(*PARK, 'b'))

    def step(self, window, step, record):
        kind = step_kind(step)
        before = self.screen.grab() if step.get('expect') else None
        if kind == 'nss':
            err = self.nss(step['nss'], step.get('timeout', 30))
            if err:
                record['failed'].append(f'nss: {err}')
        elif kind == 'key':
            self.focus()
            self.xdo('key', '--delay', 80, *step['key'].split())
        elif kind == 'type':
            self.focus()
            self.xdo('type', '--delay', 60, step['type'])
        elif kind == 'move':
            self.mouse(step, 'move')
        elif kind in ('click', 'rclick', 'dclick'):
            self.mouse(step, kind)
            time.sleep(0.2)
            button = 3 if kind == 'rclick' else 1
            self.xdo('click', '--repeat', 2 if kind == 'dclick' else 1, '--delay', 120, button)
        elif kind == 'press':
            # Held down for the pressed look; a release away from the control doesn't click it.
            self.mouse(step, 'press')
            self.xdo('mousedown', 1)
        elif kind == 'release':
            self.park()
            self.xdo('mouseup', 1)
        elif kind == 'scroll':
            if 'at' in step:
                self.mouse(step, 'at')
            n = int(step['scroll'])
            self.xdo('click', '--repeat', abs(n), '--delay', 100, 5 if n > 0 else 4)
        elif kind == 'wait':
            time.sleep(float(step['wait']))
        elif kind == 'capture':
            self.capture(window, step, record)
        elif kind == 'series':
            self.series(window, step, record)
        elif kind == 'loading':
            self.loading(window, step, record)
        if before is not None:
            ok, _ = settle(self.screen, before, step.get('limit', 20), step.get('steady', STEADY))
            if not ok:
                record['failed'].append(f'{json.dumps(step)}: the screen did not change and settle '
                                        f'(last change {settle.noise:.2f})')

    def save(self, name, record, settled=True):
        path = os.path.join(self.out, name + '.png')
        self.screen.grab(path)
        record['files'].append(os.path.basename(path))
        if not settled:
            record['failed'].append(f'{name}: the picture did not settle; saved as it was')

    def capture(self, window, step, record):
        """Save <window>-<state>.png once the screen holds still."""
        ok, _ = settle(self.screen, limit=step.get('limit', 20), steady=step.get('steady', STEADY))
        self.save(f'{window}-{step["capture"]}', record, ok)

    def series(self, window, step, record):
        """count pictures every `every` seconds: a bar filling (resting, say), as far as the game lets it."""
        for i in range(int(step.get('count', 4))):
            if i:
                time.sleep(float(step.get('every', 1.0)))
            self.save(f'{window}-{step["series"]}-{i:02d}', record)

    def loading(self, window, step, record):
        """The loading screen of an area change the step before started: up to count different pictures of it
        while it lasts (its bar at different fills), then the area once it has loaded."""
        count, kept, last, start = int(step.get('count', 4)), 0, None, time.time()
        while time.time() - start < step.get('limit', 60) and kept < count:
            thumb = self.screen.grab()
            if d.loading(thumb) and mean_diff(thumb, last) > 0.05:
                path = os.path.join(self.out, f'{window}-{step["loading"]}-{kept:02d}.png')
                last = self.screen.grab(path)
                if d.loading(last):
                    record['files'].append(os.path.basename(path))
                    kept += 1
                else:
                    os.remove(path)
            elif kept and not d.loading(thumb):
                break
            time.sleep(0.05)
        if kept < count:
            record['failed'].append(f'{window}-{step["loading"]}: {kept} of {count} loading pictures '
                                    '(the area loaded faster than that)')

    def shot(self, shot):
        record = {'window': shot['window'], 'files': [], 'failed': [],
                  'staged': [s['nss'] for s in shot.get('steps', []) if 'nss' in s]}
        d.log(f'{shot["window"]}')
        self.park()
        for step in shot.get('steps', []):
            self.step(shot['window'], step, record)
        for step in shot.get('close', []):
            self.step(shot['window'], step, record)
        self.park()
        for f in record['failed']:
            d.log(f'  {f}')
        self.manifest.append(record)
        return record


def gui_sheet(pictures, path):
    """A contact sheet of the pictures (four to a row, 480 wide), each with its name under it."""
    if not pictures:
        return
    cols = min(4, len(pictures))
    rows = -(-len(pictures) // cols)
    inputs = sum((['-i', p] for p in pictures), [])
    graph = ''
    for i, p in enumerate(pictures):
        label = os.path.basename(p)[:-4].replace(':', r'\:')
        graph += (f'[{i}:v]scale=480:270,pad=480:296:0:0:black,'
                  f"drawtext=text='{label}':x=4:y=274:fontsize=16:fontcolor=white[s{i}];")
    blank = cols * rows - len(pictures)
    graph += ''.join(f'color=black:s=480x296:d=1[b{i}];' for i in range(blank))
    labels = ''.join(f'[s{i}]' for i in range(len(pictures))) + ''.join(f'[b{i}]' for i in range(blank))
    if cols * rows == 1:
        graph = graph.rstrip(';').rsplit('[s0]', 1)[0]
    else:
        layout = '|'.join(f'{(i % cols) * 480}_{(i // cols) * 296}' for i in range(cols * rows))
        graph += labels + f'xstack=inputs={cols * rows}:layout={layout}'
    subprocess.run(['ffmpeg', '-v', 'error', '-y'] + inputs + ['-filter_complex', graph, '-frames:v', '1', path],
                   check=False)


# ---------------------------------------------------------------- the client


def gui_client(args, scale):
    """The GUI client's own user folder (args.client_dir), apart from film's: the player's CD key and content
    folders, and a settings.tml made from nothing but nwn-director's settings and this UI scale, with no key
    bindings of the player's (nwnplayer.ini is left out): every run starts from the game's own layout and keys.
    Its local vault holds only the character."""
    cdir = os.path.expanduser(args.client_dir)
    user = os.path.expanduser(args.user_dir)
    os.makedirs(cdir, exist_ok=True)
    for f in ('cdkey.ini', 'nwncdkey.ini', 'cryptographic_secret'):
        if os.path.exists(os.path.join(user, f)):
            shutil.copy2(os.path.join(user, f), os.path.join(cdir, f))
    # Left from an earlier run: the client writes window and key settings into these.
    for f in ('settings.tml', 'nwnplayer.ini', 'nwn.ini'):
        if os.path.exists(os.path.join(cdir, f)):
            os.remove(os.path.join(cdir, f))
    for name in ('hak', 'tlk', 'portraits', 'music', 'ambient', 'movies'):
        src, dst = os.path.join(user, name), os.path.join(cdir, name)
        if name == 'hak':
            src = args.hak_dir
        elif name == 'tlk':
            src = args.tlk_dir
        if os.path.lexists(dst):
            os.remove(dst)
        if os.path.exists(src):
            os.symlink(os.path.realpath(src), dst)
    settings = {**d.CLIENT_SETTINGS, **d.STILLS_SETTINGS, **GUI_SETTINGS, ('ui', 'scale'): f'{scale:.1f}'}
    d.client_settings(None, os.path.join(cdir, 'settings.tml'), settings)
    vault = os.path.join(cdir, 'localvault')
    shutil.rmtree(vault, ignore_errors=True)
    os.makedirs(vault)
    shutil.copy2(args.character, vault)
    return cdir


def start_gui_client(args, game, cdir, logfile, connect):
    """The client, drawing on the CPU with no sound; connect: straight to the server, or else the main menu."""
    binary = os.path.join(game, 'bin', 'linux-x86', 'nwmain-linux')
    env = dict(os.environ, LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe', __GLX_VENDOR_LIBRARY_NAME='mesa',
               SDL_AUDIODRIVER='dummy')
    cmd = [binary, '-userdirectory', cdir] + (['+connect', f'127.0.0.1:{args.port}'] if connect else [])
    return subprocess.Popen(cmd, cwd=os.path.dirname(binary), stdout=logfile, stderr=subprocess.STDOUT, env=env)


def start_display(screen):
    """A virtual X display of the screen's size: sets DISPLAY, returns the server."""
    n = 99
    while os.path.exists(f'/tmp/.X11-unix/X{n}') or os.path.exists(f'/tmp/.X{n}-lock'):
        n += 1
    xvfb = subprocess.Popen(['Xvfb', f':{n}', '-screen', '0', screen.size + 'x24', '-nolisten', 'tcp'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        if os.path.exists(f'/tmp/.X11-unix/X{n}'):
            os.environ['DISPLAY'] = f':{n}'
            return xvfb
        time.sleep(0.1)
    xvfb.kill()
    d.die('the virtual display (Xvfb) did not start')


def wait_drawn(screen, limit=120, in_game=False):
    """Wait for the client's first settled picture that isn't black (in_game: nor a loading screen, which the
    character list looks like to d.loading: a panel with black all round). False on timeout."""
    start = time.time()

    def ready(thumb):
        return not d.black(thumb) and not (in_game and d.loading(thumb))
    while time.time() - start < limit:
        if ready(screen.grab()):
            ok, thumb = settle(screen, limit=min(30, max(1, limit - (time.time() - start))))
            if ok and ready(thumb):
                return True
        time.sleep(0.5)
    return False


# Where the mouse waits between steps: over the chat log, from the bottom middle of the screen.
PARK = (-100, -90)

# The Play button on the character list: from the screen's centre, in logical pixels.
PLAY_BUTTON = (102, 231)


def play(runner, limit=120):
    """Press Play on the character list until the player is in the first area; then wait for its loading screen
    to go. False if that doesn't happen within limit seconds."""
    start = time.time()
    while time.time() - start < limit:
        runner.xdo('mousemove', *runner.screen.at(*PLAY_BUTTON), 'click', 1)
        time.sleep(0.3)
        runner.park()
        if runner.slog.wait_for(r'DIRECTOR: player in', 8, fatal=False):
            runner.nss(RESET)
            return wait_drawn(runner.screen, limit, in_game=True)
    return False


# Staged before every in-game shot: the player back at the start, nothing going on, and the camera looking
# straight down at the floor: no flickering torches behind the windows, so a settled picture is easy to tell.
RESET = ('ClearAllActions(TRUE); SetTime(12, 0, 0, 0);'
         'object o = GetObjectByTag("DIR_TMP"); int i = 0;'
         'while (GetIsObjectValid(o)) { DestroyObject(o); o = GetObjectByTag("DIR_TMP", ++i); }'
         'o = GetHenchman(OBJECT_SELF); while (GetIsObjectValid(o)) { RemoveHenchman(OBJECT_SELF, o); DestroyObject(o);'
         ' o = GetHenchman(OBJECT_SELF); }'
         'if (GetArea(OBJECT_SELF) != GetObjectByTag("dir_gui_a")) '
         'JumpToLocation(Location(GetObjectByTag("dir_gui_a"), Vector(10.0, 15.0, 0.0), 270.0));'
         'SetCameraFacing(90.0, 5.0, 1.0, CAMERA_TRANSITION_TYPE_SNAP);'
         'SetCameraFlags(OBJECT_SELF, CAMERA_FLAG_DISABLE_SCROLL | CAMERA_FLAG_DISABLE_TURN | CAMERA_FLAG_DISABLE_TILT'
         ' | CAMERA_FLAG_DISABLE_ZOOM);')


def module_haks(mod):
    """The hak list and custom TLK of a module (its module.ifo)."""
    with tempfile.TemporaryDirectory() as tmp:
        d.run(['nwn_erf', '--quiet', '-x', '-f', os.path.abspath(mod), 'module.ifo'], cwd=tmp)
        ifo = d.gff(os.path.join(tmp, 'module.ifo'))
    haks = [h['Mod_Hak']['value'] for h in ifo.get('Mod_HakList', {}).get('value', [])]
    return haks, ifo.get('Mod_CustomTlk', {}).get('value', '')


def hak_list(args):
    """(haks, tlk): from the module's module.ifo (SOURCE or --module), then the haks given (SOURCE or --haks:
    names or .hak files, in priority order)."""
    module, extra = args.module, args.haks or ''
    if args.source:
        if args.source.lower().endswith('.mod'):
            module = args.source
        else:
            extra = args.source + ',' + extra
    haks, tlk = module_haks(module) if module else ([], '')
    for h in extra.split(','):
        h = h.strip()
        if h:
            haks.append(os.path.basename(h)[:-4] if h.lower().endswith('.hak') else h)
    if args.tlk:
        tlk = args.tlk
    missing = [h for h in haks if not os.path.exists(os.path.join(args.hak_dir, h + '.hak'))]
    if missing:
        d.die(f'haks not in {args.hak_dir}: {", ".join(missing)} (--hak-dir)')
    return haks, tlk


PHASES = ('pregame', 'charselect', 'game')


def run_scale(args, game, shots, runner_args, scale, out):
    """Every shot at one UI scale, into out: pre-game shots on a client at the main menu, then character-list
    shots on a client that has joined the server, then in-game shots once it has pressed Play."""
    slog, db, port = runner_args
    screen = Screen(scale)
    os.makedirs(out, exist_ok=True)
    runner = Runner(screen, slog, db, out)
    runner.n = run_scale.steps
    xvfb = start_display(screen)
    # Where the client is: None, 'menu', 'charlist' or 'game'.
    client, at = None, None
    cdir = gui_client(args, scale)
    logfile = open(os.path.join(out, 'client.log'), 'a')
    try:
        for phase in PHASES:
            todo = [s for s in shots if s.get('phase', 'game') == phase]
            for i, shot in enumerate(todo):
                # A fresh client for each phase, and for a shot that must start from the phase's first screen.
                if phase == 'game' and i == 0 and client and client.poll() is None and at == 'charlist':
                    # Still at the character list: a client killed while connected keeps the CD key busy on the
                    # server for minutes, so the same one goes in.
                    if not play(runner):
                        fail_all(runner, todo, 'Play on the character list did not get the player in')
                        break
                    at = 'game'
                    runner.focus()
                    runner.park()
                    d.log(f'{phase} (UI scale {scale})')
                elif i == 0 or (shot.get('fresh') and phase == 'pregame'):
                    client = restart_client(client, args, game, cdir, logfile, phase, runner, todo[i:])
                    at = None if client is None else {'pregame': 'menu', 'charselect': 'charlist'}.get(phase, 'game')
                    if client is None:
                        break
                    if i == 0:
                        d.log(f'{phase} (UI scale {scale})')
                if client.poll() is not None:
                    fail_all(runner, [shot], f'the client stopped (exit {client.returncode})')
                    continue
                if phase == 'game':
                    runner.nss(RESET)
                    settle(screen, limit=10)
                runner.shot(shot)
        if client and client.poll() is None and at in ('charlist', 'game'):
            leave(runner, in_game=at == 'game')
    finally:
        run_scale.steps = runner.n
        if client:
            # Killed, not quit: quitting from a server shows a dialog, and the next phase starts a fresh client.
            client.kill()
            client.wait()
        xvfb.kill()
        logfile.close()
    pictures = [os.path.join(out, f) for r in runner.manifest for f in r['files']]
    gui_sheet(pictures, os.path.join(out, 'sheet.png'))
    with open(os.path.join(out, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump({'scale': scale, 'size': screen.size, 'shots': runner.manifest}, f, indent=1)
    return runner.manifest


run_scale.steps = 0


def leave(runner, in_game):
    """Take the player off the server before its client is killed, so the next client can join with the same CD
    key at once: from the character list, through Play first; the server then boots it."""
    if not in_game and not play(runner):
        return
    runner.nss('BootPC(OBJECT_SELF);')
    runner.slog.wait_for(r'Left as a Player', 20, fatal=False)


def restart_client(client, args, game, cdir, logfile, phase, runner, todo):
    """Stop client (if any) and start one for phase: at the main menu, at the character list, or in the first room.
    None, with todo failed, if it doesn't get there."""
    if client:
        client.kill()
        client.wait()
    client = start_gui_client(args, game, cdir, logfile, connect=phase != 'pregame')
    if phase != 'pregame' and not runner.slog.wait_for(r'Joined as Player', d.JOIN_WAIT, fatal=False):
        fail_all(runner, todo, 'the client never joined the server')
    elif not wait_drawn(runner.screen):
        fail_all(runner, todo, 'the client never drew a settled screen')
    elif phase == 'game' and not play(runner):
        fail_all(runner, todo, 'Play on the character list did not get the player in')
    else:
        runner.focus()
        runner.park()
        return client
    client.kill()
    client.wait()
    return None


def fail_all(runner, shots, why):
    """Every shot of shots failed for one reason; the screen as it was goes to failed-<window>.png."""
    d.log(why)
    runner.screen.grab(os.path.join(runner.out, f'failed-{shots[0]["window"]}.png'))
    for shot in shots:
        runner.manifest.append({'window': shot['window'], 'files': [], 'failed': [why],
                                'staged': [s['nss'] for s in shot.get('steps', []) if 'nss' in s]})


def cmd_gui(args):
    for tool in ('docker', 'nwn_erf', 'nwn_gff', 'nwn_script_comp', 'ffmpeg', 'xdotool', 'Xvfb'):
        if not shutil.which(tool):
            d.die(f'{tool} not found (README.md lists what is needed)')
    game = d.find_game(args.game)
    shots = load_shots(args.shots)
    if args.only:
        only = {w.strip() for w in args.only.split(',')}
        shots = [s for s in shots if s['window'] in only]
        if not shots:
            d.die(f'--only {args.only}: no such windows in {args.shots}')
    scales = parse_scales(args.scale)
    haks, tlk = hak_list(args)
    out = os.path.abspath(args.out or os.path.join('out', 'gui-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S')))
    os.makedirs(out, exist_ok=True)
    cache = os.path.expanduser('~/.cache/nwn-director')
    os.makedirs(cache, exist_ok=True)
    work = tempfile.mkdtemp(prefix='gui-', dir=cache)
    user = os.path.expanduser(args.user_dir)
    os.environ.setdefault('NWN_HOME', user if os.path.isdir(user) else work)
    slog = None
    started = time.time()
    try:
        home = make_module(game, work, haks, tlk)
        d.log(f'starting the server (haks: {", ".join(haks) or "none"})')
        d.start_server(args, home)
        slog = d.ServerLog()
        slog.wait_for(r'DIRECTOR: module loaded', 300)
        db = os.path.join(home, 'database', 'director.sqlite3')
        runs = {}
        for scale in scales:
            t = time.time()
            manifest = run_scale(args, game, shots, (slog, db, args.port), scale, os.path.join(out, scale_dir(scale)))
            runs[scale_dir(scale)] = {'seconds': round(time.time() - t), 'shots': manifest}
    finally:
        if slog:
            slog.close()
        subprocess.run(['docker', 'rm', '-f', d.CONTAINER], capture_output=True)
        d.remove_server_files(args.image, work)
        shutil.rmtree(work, ignore_errors=True)
    failed = sum(1 for r in runs.values() for s in r['shots'] if s['failed'])
    with open(os.path.join(out, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump({'haks': haks, 'tlk': tlk, 'seconds': round(time.time() - started), 'runs': runs}, f, indent=1)
    d.log(f'done in {round(time.time() - started)} s, {failed} shots with problems (manifest.json): {out}')


def parse_scales(text):
    try:
        scales = [float(s) for s in text.split(',') if s.strip()]
    except ValueError:
        scales = []
    if not scales or any(s < 1.0 or s > 2.0 for s in scales):
        d.die(f'--scale {text}: UI scales from 1 to 2, comma-separated (1,1.5)')
    return scales


def scale_dir(scale):
    return f'scale-{scale:g}'


# ---------------------------------------------------------------- comparing two runs

# Pictures compared at this size: small enough to be quick, big enough that a changed button or a font shows.
COMPARE_SIZE = (640, 360)
# A pixel counts as changed if a channel differs by more than this (0 to 255): llvmpipe draws the same frame
# the same way, but an animated torch or water moves between captures.
PIXEL_TOLERANCE = 24


def pixels(path):
    """A picture as RGB bytes at COMPARE_SIZE."""
    w, h = COMPARE_SIZE
    return subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-vf', f'scale={w}:{h}:flags=area', '-f', 'rawvideo',
                           '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True).stdout


def difference(a, b):
    """(mean, changed): the mean channel difference of two RGB pictures (0 to 255), and the share of pixels
    changed beyond PIXEL_TOLERANCE (0 to 1)."""
    if len(a) != len(b) or not a:
        return 255.0, 1.0
    total, changed = 0, 0
    for i in range(0, len(a), 3):
        dr, dg, db = abs(a[i] - b[i]), abs(a[i + 1] - b[i + 1]), abs(a[i + 2] - b[i + 2])
        total += dr + dg + db
        if max(dr, dg, db) > PIXEL_TOLERANCE:
            changed += 1
    return total / len(a), changed / (len(a) // 3)


def side_by_side(a, b, path):
    """a | b | their difference (brightened), one picture."""
    w, h = COMPARE_SIZE
    graph = (f'[0:v]scale={w}:{h},split[a][a2];[1:v]scale={w}:{h},split[b][b2];'
             f'[a2][b2]blend=all_mode=difference,eq=brightness=0.2:contrast=3[d];[a][b][d]hstack=inputs=3')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', a, '-i', b, '-filter_complex', graph, '-frames:v', '1',
                    path], check=False)


def compare(dir_a, dir_b, out):
    """Every picture the two runs share (by scale folder and name): a side-by-side with its difference, a score,
    and a report sorted most changed first (report.json), plus the pictures only one run has."""
    os.makedirs(out, exist_ok=True)
    rows, only = [], []
    scales = sorted({s for s in os.listdir(dir_a) if s.startswith('scale-')} |
                    {s for s in os.listdir(dir_b) if s.startswith('scale-')})
    for scale in scales or ['']:
        a_dir, b_dir = os.path.join(dir_a, scale), os.path.join(dir_b, scale)
        a = {f for f in os.listdir(a_dir) if f.endswith('.png') and f != 'sheet.png'} if os.path.isdir(a_dir) else set()
        b = {f for f in os.listdir(b_dir) if f.endswith('.png') and f != 'sheet.png'} if os.path.isdir(b_dir) else set()
        only += [os.path.join(scale, f) for f in sorted(a ^ b)]
        for name in sorted(a & b):
            mean, changed = difference(pixels(os.path.join(a_dir, name)), pixels(os.path.join(b_dir, name)))
            pair = (scale + '-' if scale else '') + name
            row = {'shot': os.path.join(scale, name), 'mean': round(mean, 2), 'changed': round(changed, 4)}
            if changed > 0:
                side_by_side(os.path.join(a_dir, name), os.path.join(b_dir, name), os.path.join(out, pair))
                row['pair'] = pair
            rows.append(row)
    rows.sort(key=lambda r: (-r['changed'], -r['mean']))
    with open(os.path.join(out, 'report.json'), 'w', encoding='utf-8') as f:
        json.dump({'a': os.path.abspath(dir_a), 'b': os.path.abspath(dir_b), 'shots': rows, 'only_in_one': only},
                  f, indent=1)
    return rows, only


def cmd_compare(args):
    if not shutil.which('ffmpeg'):
        d.die('ffmpeg not found')
    for p in (args.a, args.b):
        if not os.path.isdir(p):
            d.die(f'no folder {p}')
    out = os.path.abspath(args.out or os.path.join(args.b, 'compare'))
    rows, only = compare(args.a, args.b, out)
    changed = [r for r in rows if r['changed'] > 0]
    for r in changed:
        print(f'{r["changed"] * 100:6.2f}% changed  mean {r["mean"]:6.2f}  {r["shot"]}')
    for f in only:
        print(f'only in one run: {f}')
    d.log(f'{len(changed)} of {len(rows)} shots changed; side-by-sides and report.json in {out}')


def add_commands(sub, user_dir, image):
    """The gui and gui-compare commands, on director.py's subparsers."""
    g = sub.add_parser('gui', help="pictures of the game's own interface windows in each of their states")
    g.add_argument('source', nargs='?',
                   help='a module (.mod) whose haks and TLK to load, or haks, comma-separated (or neither: none)')
    g.add_argument('--character', required=True, help='the character to log in with (.bic)')
    g.add_argument('--haks', help='haks to load, in priority order: names in --hak-dir, or .hak files there')
    g.add_argument('--module', help="load this module's haks and TLK (the module itself isn't run)")
    g.add_argument('--tlk', help='custom TLK to load (name, in --tlk-dir)')
    g.add_argument('--scale', default='1,1.5', help='UI scales to run at, comma-separated (1,1.5)')
    g.add_argument('--shots', default=SHOTS_FILE, help='the shot list (gui_shots.json)')
    g.add_argument('--only', help='only these windows of the shot list, comma-separated')
    g.add_argument('--out', help='folder for the pictures (out/gui-<date-time>)')
    g.add_argument('--game', help='NWN:EE install folder (found by itself in the usual places)')
    g.add_argument('--user-dir', default=user_dir, help=f'your NWN user folder ({user_dir})')
    g.add_argument('--hak-dir', help="where the haks are (your user folder's hak)")
    g.add_argument('--tlk-dir', help="where the TLK is (your user folder's tlk)")
    g.add_argument('--client-dir', default='~/.cache/nwn-director/gui-client',
                   help="the client's own user folder (~/.cache/nwn-director/gui-client)")
    g.add_argument('--port', type=int, default=5121, help='server port on 127.0.0.1 (5121)')
    g.add_argument('--image', default=image, help=f'server Docker image ({image})')
    g.set_defaults(func=cmd_gui)

    c = sub.add_parser('gui-compare', help='two gui runs side by side, with what changed and by how much')
    c.add_argument('a', help='the first run (its out folder)')
    c.add_argument('b', help='the second run')
    c.add_argument('--out', help='folder for the side-by-sides and report.json (B/compare)')
    c.set_defaults(func=cmd_compare)
