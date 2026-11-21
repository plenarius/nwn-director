"""Tests for gui.py's plain parts (no game, server or client needed); the picture tests need ffmpeg.

    python3 -m unittest discover tests
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import gui  # noqa: E402


class ScreenAt(unittest.TestCase):
    def test_anchors_at_scale_1(self):
        s = gui.Screen(1)
        self.assertEqual(s.size, '1280x720')
        self.assertEqual(s.at(0, 0), (640, 360))
        self.assertEqual(s.at(10, 20, 'tl'), (10, 20))
        self.assertEqual(s.at(-60, 118, 'tr'), (1220, 118))
        self.assertEqual(s.at(-365, -17, 'b'), (275, 703))
        self.assertEqual(s.at(5, -5, 'bl'), (5, 715))

    def test_positions_scale_with_the_ui(self):
        s = gui.Screen(1.5)
        self.assertEqual(s.size, '1920x1080')
        self.assertEqual(s.at(100, -50), (1110, 465))
        self.assertEqual(s.at(10, 20, 'tl'), (15, 30))
        self.assertEqual(s.at(-60, 118, 'tr'), (1830, 177))


class MeanDiff(unittest.TestCase):
    def test_same_and_different(self):
        self.assertEqual(gui.mean_diff(bytes(10), bytes(10)), 0)
        self.assertEqual(gui.mean_diff(bytes(10), bytes([10] * 10)), 10)

    def test_missing_or_wrong_size_is_all_changed(self):
        self.assertEqual(gui.mean_diff(None, bytes(4)), 255.0)
        self.assertEqual(gui.mean_diff(bytes(4), None), 255.0)
        self.assertEqual(gui.mean_diff(bytes(4), bytes(5)), 255.0)


def shots_file(data):
    tmp = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
    json.dump(data, tmp)
    tmp.close()
    return tmp.name


class LoadShots(unittest.TestCase):
    def test_the_shipped_list_loads(self):
        shots = gui.load_shots(gui.SHOTS_FILE)
        names = gui.sheet_names(shots)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn('inventory-normal', names)
        self.assertIn('rest-bar-03', names)
        self.assertTrue({s.get('phase', 'game') for s in shots} <= set(gui.PHASES))

    def test_capture_names(self):
        self.assertEqual(gui.capture_names('rest', {'series': 'bar', 'count': 2}), ['rest-bar-00', 'rest-bar-01'])
        self.assertEqual(gui.capture_names('inv', {'capture': 'normal'}), ['inv-normal'])

    def test_bad_lists_die(self):
        bad = [
            {},
            {'shots': [{'window': 'Bad Name', 'steps': []}]},
            {'shots': [{'window': 'a', 'steps': [{'jump': 1}]}]},
            {'shots': [{'window': 'a', 'steps': [{'key': 'i', 'click': [0, 0]}]}]},
            {'shots': [{'window': 'a', 'steps': [{'capture': 'x'}]}, {'window': 'a', 'steps': [{'capture': 'x'}]}]},
            {'shots': [{'window': 'a', 'fresh': True, 'steps': []}]},
        ]
        for data in bad:
            path = shots_file(data)
            try:
                with self.assertRaises(SystemExit, msg=json.dumps(data)):
                    gui.load_shots(path)
            finally:
                os.remove(path)


class FakeScreen:
    """Thumbnails from a list, the last repeated."""

    def __init__(self, frames):
        self.frames = list(frames)

    def grab(self, path=None):
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]


class Settle(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        patches = [mock.patch('gui.time.time', lambda: self.now[0]),
                   mock.patch('gui.time.sleep', lambda s: self.now.__setitem__(0, self.now[0] + s))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_a_window_opens_then_holds(self):
        frames = [bytes(16)] * 2 + [bytes([50] * 16)] * 20
        ok, thumb = gui.settle(FakeScreen(frames), before=bytes(16), limit=10)
        self.assertTrue(ok)
        self.assertEqual(thumb, bytes([50] * 16))

    def test_nothing_opens(self):
        ok, _ = gui.settle(FakeScreen([bytes(16)]), before=bytes(16), limit=3)
        self.assertFalse(ok)

    def test_never_still_unless_allowed_to_move(self):
        frames = [bytes([v * 2 % 256] * 16) for v in range(200)]
        self.assertFalse(gui.settle(FakeScreen(frames), limit=5)[0])
        self.assertTrue(gui.settle(FakeScreen(frames), limit=5, steady=20)[0])


class Scales(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(gui.parse_scales('1,1.5'), [1.0, 1.5])
        self.assertEqual(gui.scale_dir(1.0), 'scale-1')
        self.assertEqual(gui.scale_dir(1.5), 'scale-1.5')
        for bad in ('', 'x', '0.5', '3'):
            with self.assertRaises(SystemExit):
                gui.parse_scales(bad)


class Module(unittest.TestCase):
    def test_rooms_and_ifo(self):
        are, git = gui.room('dir_gui_a', 'Room 1')
        self.assertEqual(len(are['Tile_List']['value']), gui.ROOM_SIZE[0] * gui.ROOM_SIZE[1])
        self.assertEqual(are['LoadScreenID']['value'], gui.LOAD_SCREEN)
        self.assertIn('Creature List', git)
        ifo = gui.module_ifo(['top', 'base'], 'custom', list(gui.GUI_AREAS))
        self.assertEqual([h['Mod_Hak']['value'] for h in ifo['Mod_HakList']['value']], ['top', 'base'])
        self.assertEqual(ifo['Mod_CustomTlk']['value'], 'custom')
        self.assertEqual(ifo['Mod_Entry_Area']['value'], 'dir_gui_a')
        self.assertEqual(ifo['Mod_OnClientEntr']['value'], 'dir_gui')

    def test_steps_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = gui.steps_db(os.path.join(tmp, 'database'))
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO steps VALUES (1, 'x')")
            path = gui.steps_db(os.path.join(tmp, 'database'))
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM steps').fetchone()[0], 0)


class HakList(unittest.TestCase):
    def args(self, tmp, **kw):
        a = dict(source=None, module=None, haks=None, tlk=None, hak_dir=tmp)
        a.update(kw)
        return argparse.Namespace(**a)

    def test_source_haks_and_option_haks(self):
        with tempfile.TemporaryDirectory() as tmp:
            for h in ('theme', 'base'):
                open(os.path.join(tmp, h + '.hak'), 'w').close()
            self.assertEqual(gui.hak_list(self.args(tmp, source='theme.hak,base')), (['theme', 'base'], ''))
            self.assertEqual(gui.hak_list(self.args(tmp, haks='base', tlk='t')), (['base'], 't'))
            with self.assertRaises(SystemExit):
                gui.hak_list(self.args(tmp, source='missing'))

    def test_source_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, 'a.hak'), 'w').close()
            with mock.patch('gui.module_haks', return_value=(['a'], 'mytlk')) as mh:
                self.assertEqual(gui.hak_list(self.args(tmp, source='x.mod')), (['a'], 'mytlk'))
                mh.assert_called_once_with('x.mod')


class Steps(unittest.TestCase):
    """The runner's input and staging, with xdotool and the server faked."""

    def runner(self, log_line='DIRECTOR: DONE 1'):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        db = gui.steps_db(os.path.join(tmp, 'database'))
        slog = mock.Mock()
        slog.wait_for.return_value = log_line
        r = gui.Runner(gui.Screen(1.5), slog, db, tmp)
        r.xdo = mock.Mock()
        return r, db

    def test_nss_goes_to_the_steps_table(self):
        r, db = self.runner()
        self.assertEqual(r.nss('SpeakString("hi");'), '')
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute('SELECT n, code FROM steps').fetchall(), [(1, 'SpeakString("hi");')])

    def test_nss_compiler_error_is_reported(self):
        r, _ = self.runner('DIRECTOR: FAIL 1 syntax error')
        self.assertEqual(r.nss('nonsense'), 'syntax error')

    def test_mouse_steps_land_at_the_scaled_position(self):
        r, _ = self.runner()
        record = {'failed': [], 'files': []}
        r.step('w', {'click': [10, 20], 'anchor': 'tl'}, record)
        r.xdo.assert_any_call('mousemove', 15, 30)
        r.step('w', {'rclick': [0, 0]}, record)
        r.xdo.assert_any_call('click', '--repeat', 1, '--delay', 120, 3)
        r.step('w', {'press': [0, 0]}, record)
        r.xdo.assert_any_call('mousedown', 1)
        r.step('w', {'scroll': -2, 'at': [0, 0]}, record)
        r.xdo.assert_any_call('click', '--repeat', 2, '--delay', 100, 4)
        self.assertEqual(record['failed'], [])

    def test_keys_focus_the_game_first(self):
        r, _ = self.runner()
        r.step('w', {'key': 'i'}, {'failed': [], 'files': []})
        calls = [c.args for c in r.xdo.call_args_list]
        self.assertEqual(calls[0][:3], ('search', '--name', 'Neverwinter Nights'))
        self.assertEqual(calls[1], ('key', '--delay', 80, 'i'))


@unittest.skipUnless(shutil.which('ffmpeg'), 'needs ffmpeg')
class Pictures(unittest.TestCase):
    def picture(self, path, colour, box=None):
        vf = f'drawbox=x=100:y=100:w=200:h=100:color={box}:t=fill' if box else 'null'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color={colour}:s=1280x720:d=1',
                        '-vf', vf, '-frames:v', '1', path], check=True)

    def test_compare_two_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            for run in ('a', 'b'):
                os.makedirs(os.path.join(tmp, run, 'scale-1'))
            self.picture(os.path.join(tmp, 'a', 'scale-1', 'inv-normal.png'), 'gray')
            self.picture(os.path.join(tmp, 'b', 'scale-1', 'inv-normal.png'), 'gray', box='red')
            self.picture(os.path.join(tmp, 'a', 'scale-1', 'map-normal.png'), 'blue')
            self.picture(os.path.join(tmp, 'b', 'scale-1', 'map-normal.png'), 'blue')
            self.picture(os.path.join(tmp, 'a', 'scale-1', 'only-a.png'), 'blue')
            out = os.path.join(tmp, 'cmp')
            rows, only = gui.compare(os.path.join(tmp, 'a'), os.path.join(tmp, 'b'), out)
            self.assertEqual([r['shot'] for r in rows], ['scale-1/inv-normal.png', 'scale-1/map-normal.png'])
            self.assertAlmostEqual(rows[0]['changed'], 200 * 100 / (1280 * 720), delta=0.005)
            self.assertEqual(rows[1]['changed'], 0)
            self.assertNotIn('pair', rows[1])
            self.assertTrue(os.path.exists(os.path.join(out, rows[0]['pair'])))
            self.assertEqual(only, ['scale-1/only-a.png'])
            with open(os.path.join(out, 'report.json')) as f:
                self.assertEqual(len(json.load(f)['shots']), 2)

    def test_a_scale_only_one_run_has_is_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            for run, scales in (('a', ('scale-1', 'scale-1.5')), ('b', ('scale-1',))):
                for sc in scales:
                    os.makedirs(os.path.join(tmp, run, sc))
                    self.picture(os.path.join(tmp, run, sc, 'x-normal.png'), 'gray')
            rows, only = gui.compare(os.path.join(tmp, 'a'), os.path.join(tmp, 'b'), os.path.join(tmp, 'cmp'))
            self.assertEqual([r['shot'] for r in rows], ['scale-1/x-normal.png'])
            self.assertEqual(only, ['scale-1.5/'])

    def test_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            pics = []
            for i, c in enumerate(('red', 'green', 'blue', 'gray', 'white')):
                pics.append(os.path.join(tmp, f'w-{c}.png'))
                self.picture(pics[-1], c)
            gui.gui_sheet(pics, os.path.join(tmp, 'sheet.png'))
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height', '-of', 'csv=p=0',
                                    os.path.join(tmp, 'sheet.png')], capture_output=True, text=True).stdout.strip()
            self.assertEqual(probe, f'{4 * 480},{2 * 296}')


if __name__ == '__main__':
    unittest.main()
