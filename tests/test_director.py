"""Tests for director.py's plain functions, on made-up area data (no game, server or client needed).

    python3 -m unittest discover tests
"""
import json
import os
import queue
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import types
import unittest
from unittest import mock

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


class WaitForAngle(unittest.TestCase):
    """wait_for_angle against a made-up screen: grab() hands out frames in turn, and time moves 0.5 s a poll."""

    def run_frames(self, frames, previous=None, limit=30):
        clock = [0.0]
        seen = []

        def grab(path=None):
            seen.append(frames[min(len(seen), len(frames) - 1)])
            return seen[-1]

        fake_time = types.SimpleNamespace(time=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        with mock.patch.object(d, 'grab', grab), mock.patch.object(d, 'time', fake_time):
            return d.wait_for_angle(limit, previous), len(seen)

    def view(self, level):
        return bytes([level] * (64 * 36))

    def test_first_angle_waits_for_the_black_then_a_steady_picture(self):
        pre, blk, shot = self.view(90), self.view(0), self.view(140)
        ok, polls = self.run_frames([pre] * 6 + [blk] * 3 + [shot] * 10)
        self.assertTrue(ok)
        self.assertEqual(polls, 9 + 3)   # the shot, held for a second: three polls

    def test_first_angle_never_black_saved_after_the_grace(self):
        # A slow client that never drew the black: a picture that holds will do after FIRST_STILL_GRACE.
        ok, polls = self.run_frames([self.view(90)] * 100, limit=60)
        self.assertTrue(ok)
        self.assertEqual(polls, int(d.FIRST_STILL_GRACE / 0.5) + 3)

    def test_first_angle_never_black_within_the_grace(self):
        self.assertEqual(self.run_frames([self.view(90)] * 100, limit=d.FIRST_STILL_GRACE - 1)[0], False)

    def test_a_camera_easing_in_is_waited_out(self):
        # 1.0 a poll: under 1.5 from one frame to the next, 2.0 over a second.
        easing = [self.view(100 + i) for i in range(8)]
        ok, polls = self.run_frames([self.view(0)] + easing + [self.view(108)] * 10)
        self.assertTrue(ok)
        self.assertGreater(polls, 1 + 8)

    def test_later_angle_once_turned_and_held(self):
        last, turned = self.view(90), self.view(130)
        ok, polls = self.run_frames([last] * 4 + [turned] * 10, previous=last)
        self.assertTrue(ok)
        self.assertEqual(polls, 4 + 3)

    def test_later_angle_never_turned(self):
        last = self.view(90)
        self.assertEqual(self.run_frames([last] * 5, previous=last)[0], False)

    def test_moving_scene_holds_to_the_looser_limit_later(self):
        # Mist: 3 or 6 from frame to frame, after the camera has turned from 90 to 130.
        last = self.view(90)
        mist = [self.view(130 + 3 * (i % 3)) for i in range(200)]
        ok, polls = self.run_frames([last] * 4 + mist, previous=last, limit=60)
        self.assertTrue(ok)
        self.assertEqual(polls, int(d.STILL_MOVING_AFTER / 0.5) + 1)   # the first poll at the looser limit

    def test_moving_scene_never_turned(self):
        last = [self.view(90 + 3 * (i % 3)) for i in range(200)]
        self.assertEqual(self.run_frames(last, previous=self.view(90), limit=60)[0], False)


class LogIn(unittest.TestCase):
    """log_in against a made-up server log: each wait for "Joined" gives the next of joins (None: not joined)."""

    def run_log_in(self, joins, vault_key=None):
        joins = list(joins)
        restarts = []

        class FakeLog:
            def wait_for(self, pattern, timeout, fatal=True):
                if 'Joined' in pattern:
                    return joins.pop(0) if joins else None
                return 'DIRECTOR: cameraman in: Bardo'

        with mock.patch.object(d, 'subprocess', mock.Mock()), mock.patch.object(d, 'grab', mock.Mock()), \
                mock.patch.object(d.time, 'sleep', lambda s: None), mock.patch.object(d, 'log', lambda m: None):
            d.log_in(FakeLog(), '960x540', vault_key=vault_key, raise_window=False,
                     restart=lambda: restarts.append(1))
        return len(restarts)

    def test_joins_first_time(self):
        self.assertEqual(self.run_log_in(['director (KEY1) Joined as Player 1']), 0)

    def test_refused_once_then_restarted_and_in(self):
        self.assertEqual(self.run_log_in([None, 'director (KEY1) Joined as Player 1']), 1)

    def test_never_joins(self):
        with self.assertRaises(SystemExit) as e:
            self.run_log_in([None, None])
        self.assertIn('never logged in', str(e.exception))

    def test_character_in_another_keys_vault(self):
        with self.assertRaises(SystemExit) as e:
            self.run_log_in(['director (KEY2) Joined as Player 1'], vault_key='KEY1')
        self.assertIn('KEY2', str(e.exception))


class StillsDb(unittest.TestCase):
    def test_fresh_count_and_writable(self):
        tmp = tempfile.mkdtemp()
        try:
            path = d.stills_db(os.path.join(tmp, 'database'))
            with sqlite3.connect(path) as db:
                db.execute('UPDATE stills SET n = n + 1')
            path = d.stills_db(os.path.join(tmp, 'database'))   # made fresh again
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT n FROM stills').fetchall(), [(0,)])
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o666)
        finally:
            shutil.rmtree(tmp)


class VirtualGL(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def make(self, *paths):
        """Files under the temporary folder (executable); returns the first's path."""
        for p in paths:
            path = os.path.join(self.dir, p)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, 'w').close()
            os.chmod(path, 0o755)
        return os.path.join(self.dir, paths[0])

    def test_unpacked_deb_gets_its_fakers_folder(self):
        vglrun = self.make('opt/VirtualGL/bin/vglrun', 'usr/lib/libvglfaker.so')
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('VGL_DISPLAY', None)
            self.assertEqual(d.vgl_command(vglrun),
                             [vglrun, '-d', 'egl', '-ld', os.path.join(self.dir, 'usr', 'lib')])

    def test_build_with_lib64_next_to_bin(self):
        vglrun = self.make('vgl/bin/vglrun', 'vgl/lib64/libvglfaker.so')
        self.assertEqual(d.vgl_command(vglrun)[3:], ['-ld', os.path.join(self.dir, 'vgl', 'lib64')])

    def test_no_fakers_found_and_another_device(self):
        vglrun = self.make('bin/vglrun')
        with mock.patch.dict(os.environ, {'VGL_DISPLAY': '/dev/dri/card1'}):
            self.assertEqual(d.vgl_command(vglrun), [vglrun, '-d', '/dev/dri/card1'])

    def test_found_by_option_then_variable_then_known_places(self):
        mine, theirs, known = (self.make(p) for p in ('a/vglrun', 'b/vglrun', 'c/vglrun'))
        with mock.patch.dict(os.environ, {'NWN_DIRECTOR_VGLRUN': theirs, 'PATH': self.dir}), \
                mock.patch.object(d, 'VGLRUN_PATHS', [known]):
            self.assertEqual(d.find_vglrun(mine), mine)
            self.assertEqual(d.find_vglrun(None), theirs)
            del os.environ['NWN_DIRECTOR_VGLRUN']
            self.assertEqual(d.find_vglrun(None), known)
        with mock.patch.dict(os.environ, {'PATH': self.dir}), mock.patch.object(d, 'VGLRUN_PATHS', []):
            os.environ.pop('NWN_DIRECTOR_VGLRUN', None)
            self.assertIsNone(d.find_vglrun(None))

    def test_one_given_that_isnt_there_dies(self):
        with self.assertRaises(SystemExit) as e:
            d.find_vglrun(os.path.join(self.dir, 'nothing'))
        self.assertIn('no vglrun', str(e.exception))


class ClientSettings(unittest.TestCase):
    settings ={('graphics.grass', 'mode'): '0', ('graphics.fbo.ssao', 'enabled'): 'false',
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


class ClientAliases(unittest.TestCase):
    def test_player_folders_moved_to_client(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        ini = os.path.join(tmp, 'nwn.ini')
        with open(ini, 'w') as fh:
            fh.write('[Alias]\nSAVES=/home/p/NWN/saves\nHD0=/home/p/NWN/\nHAK=/srv/haks\n'
                     'LOGS=/home/p/NWN2/logs\n[Display Options]\nSAVES=/home/p/NWN/saves\n')
        d.client_aliases(ini, '/home/p/NWN', '/c')
        with open(ini) as fh:
            self.assertEqual(fh.read(), '[Alias]\nSAVES=/c/saves\nHD0=/c/\nHAK=/srv/haks\nLOGS=/home/p/NWN2/logs\n'
                                        '[Display Options]\nSAVES=/home/p/NWN/saves\n')


class VaultCharacter(unittest.TestCase):
    def test_only_the_character_under_our_name(self):
        tmp = tempfile.mkdtemp()
        try:
            char = os.path.join(tmp, 'barbarian.bic')   # a pregenerated character's name
            with open(char, 'wb') as f:
                f.write(b'BIC V3.2')
            vault = os.path.join(tmp, 'client', 'localvault')
            os.makedirs(vault)
            open(os.path.join(vault, 'old.bic'), 'wb').close()
            d.vault_character(os.path.join(tmp, 'client'), char)
            self.assertEqual(os.listdir(vault), [d.VAULT_NAME])
            with open(os.path.join(vault, d.VAULT_NAME), 'rb') as f:
                self.assertEqual(f.read(), b'BIC V3.2')
            self.assertLessEqual(len(os.path.splitext(d.VAULT_NAME)[0]), 16)
        finally:
            shutil.rmtree(tmp)


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


class Looks(unittest.TestCase):
    consts = {'TILE_MAIN_LIGHT_COLOR_PALE_ORANGE': 30, 'TILE_MAIN_LIGHT_COLOR_PALE_DARK_BLUE': 16,
              'VFX_DUR_LIGHT_BLUE_20': 156, 'VFX_IMP_LIGHTNING_M': 74, 'WEATHER_RAIN': 1}
    hours = {'dawn': 6, 'dusk': 18, 'day': 10, 'night': 0, 'minutes': 2}

    def write(self, looks):
        fd, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(fd, 'w') as fh:
            json.dump(looks, fh)
        self.addCleanup(os.remove, path)
        return path

    def test_shipped_looks_resolve(self):
        looks = d.load_looks(d.LOOKS_FILE)
        self.assertEqual(set(looks), {'golden-hour', 'misty-dawn', 'moonlit', 'storm'})
        for lid, look in looks.items():
            with self.subTest(look=lid):
                json.dumps(d.look_script(lid, look, self.hours, self.consts))

    def test_golden_hour(self):
        s = d.look_script('golden-hour', d.load_looks(d.LOOKS_FILE)['golden-hour'], self.hours, self.consts)
        self.assertEqual(s['hour'], 18)
        self.assertEqual(s['fog'], {'color': 0xE0A050, 'amount': 12, 'fade': 8.0})
        self.assertEqual(s['tileLight'], 30)
        self.assertNotIn('lightning', s)
        self.assertNotIn('runInto', s)

    def test_dawn_runs_into_the_hour(self):
        s = d.look_script('misty-dawn', d.load_looks(d.LOOKS_FILE)['misty-dawn'], self.hours, self.consts)
        self.assertEqual(s['hour'], 6)
        self.assertEqual(s['runInto'], {'hour': 5, 'minute': 1, 'second': 58})

    def test_storm(self):
        s = d.look_script('storm', d.load_looks(d.LOOKS_FILE)['storm'], self.hours, self.consts)
        self.assertEqual((s['hour'], s['weather'], s['sky']), (10, 1, 2))
        self.assertEqual(s['lightning'], {'effect': 74, 'everyMin': 4.0, 'everyMax': 9.0, 'distMin': 15.0,
                                          'distMax': 30.0})
        self.assertEqual(s['wind']['magnitude'], 2.0)

    def test_sky_names_a_row(self):
        looks = d.load_looks(d.LOOKS_FILE, ['moonlit=37'])
        self.assertEqual(looks['moonlit']['sky'], 37)
        for bad in ('moonlit', 'moonlit=sky', 'nolook=3', 'moonlit=256'):
            with self.subTest(sky=bad), self.assertRaises(SystemExit):
                d.load_looks(d.LOOKS_FILE, [bad])

    def test_unknown_key_or_no_light_dies(self):
        light = {'ambient': '#000000', 'diffuse': '#ffffff', 'fog': '#808080', 'fogAmount': 5, 'shadows': True,
                 'fogClip': 100}
        self.assertIn('a', d.load_looks(self.write({'a': {'light': light}})))
        for looks in ({'a': {'light': light, 'skye': 3}}, {'a': {'light': {**light, 'sun': 1}}}, {'a': {}},
                      {'A b': {'light': light}}):
            with self.subTest(looks=looks), self.assertRaises(SystemExit):
                d.load_looks(self.write(looks))

    def test_unknown_constant_or_time_dies(self):
        for look in ({'tileLight': 'TILE_MAIN_LIGHT_COLOR_PLAID'}, {'time': 'teatime'}, {'time': 24},
                     {'time': 'minutes'}, {'fog': {'color': 'orange'}}):
            with self.subTest(look=look), self.assertRaises(SystemExit):
                d.look_script('x', look, self.hours, self.consts)

    def test_names(self):
        looks = d.load_looks(d.LOOKS_FILE)
        self.assertEqual(d.look_names('none, storm,storm', looks), [None, 'storm'])
        self.assertEqual(d.look_names(None, looks), [])
        with self.assertRaises(SystemExit):
            d.look_names('sunset', looks)

    def test_hours_follow_the_module(self):
        self.assertEqual(d.look_hours({}), self.hours)
        ifo = {'Mod_DawnHour': {'value': 8}, 'Mod_DuskHour': {'value': 20}, 'Mod_MinPerHour': {'value': 5}}
        self.assertEqual(d.look_hours(ifo), {'dawn': 8, 'dusk': 20, 'day': 10, 'night': 2, 'minutes': 5})
        ifo = {'Mod_DawnHour': {'value': 11}, 'Mod_DuskHour': {'value': 21}}
        self.assertEqual(d.look_hours(ifo)['day'], 16)

    def test_constants(self):
        text = 'int FOO = 3;\n  int BAR_2   = -1 ;\nfloat PI = 3.14;\n// int NOT = 4;\nint lower = 5;\n'
        self.assertEqual(d.nwscript_constants(text), {'FOO': 3, 'BAR_2': -1})

    def test_colours(self):
        self.assertEqual(d.rgb('#E0A050'), 0xE0A050)
        self.assertEqual(d.bgr('#e0a050'), 0x50A0E0)

    def test_area_copy_has_the_looks_light_in_sun_and_moon(self):
        are = {'SunAmbientColor': {'type': 'dword', 'value': 1}, 'DayNightCycle': {'type': 'byte', 'value': 0},
               'IsNight': {'type': 'byte', 'value': 1}, 'SkyBox': {'type': 'byte', 'value': 0},
               'ResRef': {'type': 'resref', 'value': 'town'}, 'Tag': {'type': 'cexostring', 'value': 'Town'}}
        look = d.load_looks(d.LOOKS_FILE)['golden-hour']
        out = d.look_are(are, look, 'dir_lk0')
        for p in ('Sun', 'Moon'):
            self.assertEqual(out[f'{p}AmbientColor'], {'type': 'dword', 'value': 0x3C4F5B})
            self.assertEqual(out[f'{p}DiffuseColor']['value'], 0x6FC8E1)
            self.assertEqual(out[f'{p}FogAmount'], {'type': 'byte', 'value': 10})
            self.assertEqual(out[f'{p}Shadows']['value'], 1)
        self.assertEqual((out['DayNightCycle']['value'], out['IsNight']['value'], out['SkyBox']['value']), (1, 0, 1))
        self.assertEqual((out['ResRef']['value'], out['Tag']['value']), ('dir_lk0', 'dir_lk0'))
        self.assertEqual(out['FogClipDist'], {'type': 'float', 'value': 150.0})

    def test_shots_in_looks_one_copy_per_area_and_look(self):
        table = d.load_looks(d.LOOKS_FILE)
        copies = {}
        spot = {'name': 'town-10-20', 'area': 'town', 'kind': 'spot', 'at': [10.0, 20.0], 'encounters': []}
        shots = d.shots_in_looks(spot, [None, 'storm', 'moonlit'], table, self.hours, self.consts, copies)
        shots += d.shots_in_looks({**spot, 'name': 'town-1-2'}, ['storm'], table, self.hours, self.consts, copies)
        self.assertEqual([(s['name'], s['area']) for s in shots],
                         [('town-10-20', 'town'), ('town-10-20-storm', 'dir_lk0'), ('town-10-20-moonlit', 'dir_lk1'),
                          ('town-1-2-storm', 'dir_lk0')])
        self.assertNotIn('look', shots[0])
        self.assertEqual(shots[1]['look']['id'], 'storm')
        self.assertEqual(copies, {('town', 'storm'): 'dir_lk0', ('town', 'moonlit'): 'dir_lk1'})


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
