"""Tests for director.py's plain functions, on made-up area data (no game, server or client needed).

    python3 -m unittest discover tests
"""
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import tomllib
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import director as d  # noqa: E402


def f(v):
    return {'type': 'float', 'value': v}


def door(x, y, bearing, linked='elsewhere'):
    return {'X': f(x), 'Y': f(y), 'Bearing': f(bearing), 'LinkedTo': {'value': linked}}


def trigger(x, y, points, linked='elsewhere'):
    return {'XPosition': f(x), 'YPosition': f(y), 'LinkedTo': {'value': linked},
            'Geometry': {'value': [{'PointX': f(px), 'PointY': f(py)} for px, py in points]}}


def encounter(x, y, points, spawns=()):
    return {'XPosition': f(x), 'YPosition': f(y),
            'Geometry': {'value': [{'X': f(px), 'Y': f(py)} for px, py in points]},
            'SpawnPointList': {'value': [{'X': f(sx), 'Y': f(sy)} for sx, sy in spawns]}}


def creature(x, y, tag='c'):
    return {'XPosition': f(x), 'YPosition': f(y), 'Tag': {'value': tag}}


class Exits(unittest.TestCase):
    def test_door_points_in_front_and_behind(self):
        (p,) = d.exits({'Door List': {'value': [door(10, 20, 0.0)]}})
        self.assertAlmostEqual(p[0], 12.5)
        self.assertAlmostEqual(p[1], 20)
        self.assertAlmostEqual(p[2], 7.5)
        self.assertAlmostEqual(p[3], 20)

    def test_unlinked_door_is_no_exit(self):
        self.assertEqual(d.exits({'Door List': {'value': [door(1, 1, 0.0, linked='')]}}), [])

    def test_trigger_is_its_shapes_middle(self):
        (p,) = d.exits({'TriggerList': {'value': [trigger(100, 50, [(0, 0), (4, 0), (4, 2), (0, 2)])]}})
        self.assertEqual(p, (102, 51))


class Route(unittest.TestCase):
    def test_no_exits_no_route(self):
        self.assertEqual(d.route({'Creature List': {'value': [creature(5, 5)]}}), [])

    def test_nearest_first_from_the_first_exit_and_close_stops_merged(self):
        git = {'TriggerList': {'value': [trigger(0, 0, [(0, 0)])]},
               'Creature List': {'value': [creature(50, 0), creature(10, 0), creature(12, 0), creature(30, 0)]}}
        self.assertEqual([p[:2] for p in d.route(git)], [[0, 0], [10, 0], [30, 0], [50, 0]])

    def test_encounter_spawn_points_are_stops(self):
        git = {'TriggerList': {'value': [trigger(0, 0, [(0, 0)])]},
               'Encounter List': {'value': [encounter(0, 0, [(0, 0)], spawns=[(20, 0)])]}}
        self.assertEqual([p[:2] for p in d.route(git)], [[0, 0], [20, 0]])


class Encounters(unittest.TestCase):
    git = {'Encounter List': {'value': [encounter(0, 0, [(0, 0), (2, 0), (2, 2), (0, 2)]),
                                        encounter(100, 100, [(0, 0), (2, 2)])]},
           'Placeable List': {'value': [{'X': f(99), 'Y': f(99), 'Tag': {'value': 'chest'}}]}}

    def test_middles(self):
        self.assertEqual(d.encounters(self.git), [[1, 1], [101, 101]])

    def test_only_near_a_spot(self):
        self.assertEqual(d.encounters(self.git, near=(5, 5)), [[1, 1]])

    def test_spot_by_tag_finds_the_tagged_objects_encounters(self):
        shot = d.spot_shot('a', self.git, 'chest')
        self.assertEqual(shot['encounters'], [[101, 101]])

    def test_spot_by_unknown_tag_keeps_them_all(self):
        self.assertEqual(len(d.spot_shot('a', self.git, 'nobody')['encounters']), 2)


class SpotShot(unittest.TestCase):
    def test_point_facing_and_name(self):
        shot = d.spot_shot('fort', {}, '213.99,60.32/510')
        self.assertEqual(shot['at'], [213.99, 60.32])
        self.assertEqual(shot['facing'], 150.0)
        self.assertEqual(shot['name'], 'fort-214-60')

    def test_tag_and_event(self):
        shot = d.spot_shot('fort', {}, 'Inn_Door', event='dir_ev0')
        self.assertEqual(shot['tag'], 'Inn_Door')
        self.assertEqual(shot['name'], 'fort-inn_door-event')
        self.assertEqual(shot['event'], 'dir_ev0')
        self.assertNotIn('facing', shot)


class AreaByName(unittest.TestCase):
    areas = {'fort': '/x/fort', 'lake': '/x/lake'}

    def test_resref_any_case(self):
        self.assertEqual(d.area_by_name('FORT', self.areas, {}), 'fort')

    def test_tag_exact_then_any_case(self):
        tags = {'fort': 'FortHope_FrH', 'lake': 'Lake'}
        self.assertEqual(d.area_by_name('FortHope_FrH', self.areas, tags), 'fort')
        self.assertEqual(d.area_by_name('forthope_frh', self.areas, tags), 'fort')

    def test_unknown_or_shared_tag_dies(self):
        with self.assertRaises(SystemExit):
            d.area_by_name('nowhere', self.areas, {'fort': 'A', 'lake': 'B'})
        with self.assertRaises(SystemExit):
            d.area_by_name('Same', self.areas, {'fort': 'Same', 'lake': 'Same'})


class Loading(unittest.TestCase):
    def thumb(self, middle=0, edge=0, top_text=False):
        """A picture in the middle (rows 4-31, columns 12-51) on edge, maybe an area's name across the top."""
        rows = []
        for r in range(36):
            inner = middle if 4 <= r < 32 else edge
            row = [edge] * 12 + [inner] * 40 + [edge] * 12
            if top_text and r < 4:
                row[20:44] = [200] * 24   # clear of the corners
            rows += row
        return bytes(rows)

    def test_loading_screen(self):
        self.assertTrue(d.loading(self.thumb(middle=120)))
        self.assertTrue(d.loading(self.thumb(middle=120, top_text=True)))

    def test_game_view(self):
        self.assertFalse(d.loading(self.thumb(middle=120, edge=60)))

    def test_wrong_size(self):
        self.assertFalse(d.loading(b''))


class ClientSettings(unittest.TestCase):
    settings = {('graphics.grass', 'mode'): '0', ('graphics.fbo.ssao', 'enabled'): 'false',
                ('graphics.new', 'thing'): '"x"'}

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_player_file_changed_and_missing_keys_added(self):
        src = os.path.join(self.dir, 'src.tml')
        with open(src, 'w') as fh:
            fh.write('[graphics]\n\tgamma = 1.6\n\t[graphics.grass]\n\t\tmode = 2\n\t[graphics.fbo.ssao]\n'
                     '\t\tradius = 3\n["~~schema"]\n\tx = 1')
        dst = os.path.join(self.dir, 'dst.tml')
        d.client_settings(src, dst, self.settings)
        with open(dst, 'rb') as fh:
            t = tomllib.load(fh)
        self.assertEqual(t['graphics']['gamma'], 1.6)
        self.assertEqual(t['graphics']['grass']['mode'], 0)
        self.assertEqual(t['graphics']['fbo']['ssao'], {'radius': 3, 'enabled': False})
        self.assertEqual(t['graphics']['new']['thing'], 'x')
        self.assertEqual(t['~~schema']['x'], 1)

    def test_no_player_file(self):
        dst = os.path.join(self.dir, 'dst.tml')
        d.client_settings(os.path.join(self.dir, 'none.tml'), dst, self.settings)
        with open(dst, 'rb') as fh:
            t = tomllib.load(fh)
        self.assertEqual(t['client']['identity']['name'], 'director')
        self.assertEqual(t['graphics']['grass']['mode'], 0)


class ServerLogLines(unittest.TestCase):
    def log(self, *lines):
        slog = d.ServerLog.__new__(d.ServerLog)
        slog.queue = queue.Queue()
        for line in lines:
            slog.queue.put(line)
        return slog

    def test_blank_line_is_not_a_timeout(self):
        slog = self.log('', 'DIRECTOR: END')
        self.assertEqual(list(d.markers(slog, 1)), [('END', '')])

    def test_quiet_log_dies(self):
        with self.assertRaises(SystemExit):
            list(self.log('DIRECTOR: ROLL x').lines(0.1))

    def test_server_gone_dies(self):
        with self.assertRaises(SystemExit):
            self.log(None).next(1)

    def test_wait_for(self):
        line = 'b (KEY1) Joined as Player 1'
        self.assertEqual(self.log('a', line).wait_for('Joined', 1), line)
        self.assertIsNone(self.log('a').wait_for('Joined', 0.1, fatal=False))


class ShotSpecs(unittest.TestCase):
    def args(self, **kw):
        base = dict(areas=None, spot=[], event=[], stills=0)
        return types.SimpleNamespace(**{**base, **kw})

    def test_order_and_parts(self):
        specs = d.shot_specs(self.args(areas='a, b', spot=['c@1,2/90'], event=['e@door=SpeakString("hi");']))
        self.assertEqual(specs, [('walk', 'a', None, None), ('walk', 'b', None, None), ('spot', 'c', '1,2/90', None),
                                 ('spot', 'e', 'door', 'SpeakString("hi");')])

    def test_bad_input_dies(self):
        for kw in (dict(), dict(spot=['nowhere']), dict(event=['a@b']), dict(spot=['a@b'], stills=-1),
                   dict(areas='a', stills=4)):
            with self.subTest(kw=kw), self.assertRaises(SystemExit):
                d.shot_specs(self.args(**kw))


@unittest.skipUnless(shutil.which('ffmpeg'), 'needs ffmpeg')
class ContactSheet(unittest.TestCase):
    def test_rows_of_four_padded(self):
        tmp = tempfile.mkdtemp()
        try:
            stills = []
            for i in range(5):
                stills.append(os.path.join(tmp, f's-{i:02d}.png'))
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=96x54', '-frames:v', '1',
                                stills[-1]], check=True)
            sheet = os.path.join(tmp, 's-sheet.png')
            d.contact_sheet(stills, sheet)
            size = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height', '-of', 'csv=p=0',
                                   sheet], capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(size, '2560,720')
        finally:
            shutil.rmtree(tmp)


if __name__ == '__main__':
    unittest.main()
