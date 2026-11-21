"""GUI shots: pictures of the game client's own (non-NUI) windows in each of their states.

    director.py gui --character CHAR.bic [--haks HAK[,HAK...]] [--module MOD] [--scale 1,1.5] [--out DIR]
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


def room(resref, name):
    """A room of ROOM_TILES as .are and .git (nwn_gff JSON)."""
    tiles = [{'__struct_id': 1, 'Tile_ID': field('int', t), 'Tile_Orientation': field('int', o),
              'Tile_Height': field('int', 0), 'Tile_MainLight1': field('byte', 0),
              'Tile_MainLight2': field('byte', 0), 'Tile_SrcLight1': field('byte', 8),
              'Tile_SrcLight2': field('byte', 0), 'Tile_AnimLoop1': field('byte', 1),
              'Tile_AnimLoop2': field('byte', 1), 'Tile_AnimLoop3': field('byte', 1)} for t, o in ROOM_TILES]
    are = {'__data_type': 'ARE ', 'Version': field('dword', 18), 'ResRef': field('resref', resref),
           'Tag': field('cexostring', resref), 'Name': field('cexolocstring', {'0': name}),
           'Comments': field('cexostring', ''), 'Creator_ID': field('int', -1), 'ID': field('int', -1),
           'Width': field('int', ROOM_SIZE[0]), 'Height': field('int', ROOM_SIZE[1]),
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


def make_module(game, work, haks, tlk):
    """The GUI module: two rooms, dir_gui, the given haks (names, no .hak) and custom TLK. Returns its path."""
    src = os.path.join(work, 'module')
    os.makedirs(src, exist_ok=True)
    for i, resref in enumerate(GUI_AREAS):
        are, git = room(resref, f'Room {i + 1}')
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
    if not a or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


# A window opening changes the thumbnail by this much on average (a small window: a few units); the cursor
# blinking in a text box or a torch flickering, under 0.3.
CHANGED = 0.4
STEADY = 0.15
# Seconds a picture must hold to count as settled.
HOLD = 1.0


def settle(screen, before=None, limit=30.0):
    """Wait until the screen has changed from before (if given) and then holds still for HOLD seconds, polling
    the screen. Returns (changed, thumbnail): changed is False if it never changed from before within limit (the
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
                if mean_diff(thumb, old[-1][1]) <= STEADY:
                    return True, thumb
                held = [(now, thumb)]
        time.sleep(0.2)
    return False, screen.grab()


# ---------------------------------------------------------------- the shot list


STEP_KINDS = ('nss', 'key', 'type', 'move', 'click', 'rclick', 'dclick', 'scroll', 'wait', 'capture', 'series',
              'loading')


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

    def mouse(self, step, key):
        pos = step[key]
        x, y = self.screen.at(pos[0], pos[1], step.get('anchor', 'c'))
        self.xdo('mousemove', x, y)
        return x, y

    def park(self):
        """The mouse out of the way: bottom right corner, over nothing the game draws a hover for."""
        self.xdo('mousemove', self.screen.w - 1, self.screen.h - 1)

    def step(self, window, step, record):
        kind = step_kind(step)
        before = self.screen.grab() if step.get('expect') else None
        if kind == 'nss':
            err = self.nss(step['nss'], step.get('timeout', 30))
            if err:
                record['failed'].append(f'nss: {err}')
        elif kind == 'key':
            self.xdo('key', '--delay', 80, *step['key'].split())
        elif kind == 'type':
            self.xdo('type', '--delay', 60, step['type'])
        elif kind == 'move':
            self.mouse(step, 'move')
        elif kind in ('click', 'rclick', 'dclick'):
            self.mouse(step, kind)
            time.sleep(0.2)
            button = 3 if kind == 'rclick' else 1
            self.xdo('click', '--repeat', 2 if kind == 'dclick' else 1, '--delay', 120, button)
        elif kind == 'scroll':
            self.mouse(step, 'at') if 'at' in step else None
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
            ok, _ = settle(self.screen, before, step.get('limit', 20))
            if not ok:
                record['failed'].append(f'{json.dumps(step)}: nothing changed on screen')

    def save(self, name, record, settled=True):
        path = os.path.join(self.out, name + '.png')
        self.screen.grab(path)
        record['files'].append(os.path.basename(path))
        if not settled:
            record['failed'].append(f'{name}: the picture did not settle; saved as it was')

    def capture(self, window, step, record):
        """Save <window>-<state>.png once the screen holds still."""
        if step.get('park', True) and not step.get('keep_mouse'):
            pass
        ok, _ = settle(self.screen, limit=step.get('limit', 20))
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
