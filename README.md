# nwn-director

Scripted camera capture for Neverwinter Nights: Enhanced Edition modules.

Give it a module and what to film. It runs the module on a local server, logs your game client
in as an invisible cameraman, and films each shot: one video per shot, 1080p60 with the game's
sound.

- **Walkthroughs** of whole areas, to cut into a trailer or a montage.
- **Spots**: a slow circle around one place (a point, or an object by tag), to see what is
  really there when triaging a bug report. Give it the area tag, position and facing from the
  report, and the circle starts looking where the player looked.
- **Events**: a spot where something happens a few seconds in: a script of the module, or a
  line of NWScript you give it (open a door, cast a spell, spawn a creature), so the clip shows
  what the engine really does.

Any shot can be filmed in a **look** (`--look`): golden hour, misty dawn, moonlit or storm. See [Looks](#looks).

Spots can also be taken as **stills** (`--stills N`): N pictures around the place and a contact
sheet, from a game client drawing on the CPU in a virtual display of its own. That needs no GPU
and leaves your desktop alone, so it can run on a server.

**GUI shots** (`director.py gui`) are pictures of the game's own interface (the old, non-NUI windows) in each
of their states, so a hak that replaces interface textures or fonts can be checked by comparing pictures instead
of by playing. See [GUI shots](#gui-shots).

## What it does

1. **Copies the module** (your `.mod` is never changed) with nwn-director's script `dir_load` as
   its OnModuleLoad (the module's own load script still runs) and as the spawn script of an
   invisible anchor creature in the first area to film. The anchor starts the director while the
   module loads, so it works even when a framework (like the Core Framework) takes over the
   module's events, or when the module's own load script stops partway.
2. **Plans a route through each area** from the module's own area files: every exit, every
   placed creature and every encounter spawn point, nearest first, starting from an exit.
3. **Runs the copy on a plain server** in Docker (no NWNX, no database), on `127.0.0.1` only.
4. **Starts your game client** from its own settings folder (made from yours, so your settings
   are never touched) and logs in with the character you give it. With VirtualGL installed, the
   client runs off screen, drawing on the GPU in a virtual display of its own, and your desktop is
   left alone ([Filming off screen](#filming-off-screen)); without it, on your desktop.
5. **Films each shot:** the camera follows an invisible walker (appearance 298,
   `Invisible_Human_Male`, base game). For a walkthrough it walks the route and the camera
   follows from behind at eye level; for a spot it stands at the place and the camera circles it
   once. The cameraman is
   invisible and ethereal (Greater Sanctuary), so nothing notices or attacks it. Before rolling,
   it steps into every encounter so the area's monsters and wildlife spawn. The time is
   set to 10:00 with clear weather, or to the shot's [look](#looks).
6. **Records** each shot with ffmpeg (`<area>.mp4` for a walkthrough, `<area>-<x>-<y>.mp4` or
   `<area>-<tag>.mp4` for a spot, with `-event` added for an event), then closes everything.

   With `--stills N`, the client runs in a virtual display of its own (Xvfb) at 960x540 and draws
   on the CPU, with no sound and with the settings that cost most on a CPU turned down or off
   (anti-aliasing, high-quality shaders and lighting, grass, shadows, screen effects). For each
   spot the camera turns through N angles; the server holds each one until the picture has
   turned and been saved (`<name>-00.png` ...), then turns to the next. Last comes a contact
   sheet of them all (`<name>-sheet.png`). On 48 cores, 12 stills take about 30 seconds after
   the area loads; on 4 busy cores, 8 stills of a town take about 3 minutes.

## Requirements

- **Linux.** To film off screen on the GPU: **[VirtualGL](https://virtualgl.org)** 3.x and
  **Xvfb** ([Filming off screen](#filming-off-screen)). Without VirtualGL (or with `--desktop`), an
  X11 desktop (recording uses `x11grab`), unlocked while it films; it keeps the desktop from idling
  into the lock screen while it runs (GNOME). For `--stills`, no desktop and no GPU: **Xvfb** and
  Mesa's software renderer (llvmpipe).
- **NWN:EE** (Steam, GOG or Beamdog), and the module's haks and TLK in your user folder (or
  give their folders).
- **Docker**, to run the server (`ghcr.io/urothis/nwserver`, the community server image). The
  server checks your client's CD key online, so it needs internet access.
- **[neverwinter.nim](https://github.com/niv/neverwinter.nim)** tools on your `PATH`:
  `nwn_erf`, `nwn_gff`, `nwn_script_comp`.
- **ffmpeg**, **xdotool**, **xrandr** (on the desktop), and **pactl** (PipeWire or PulseAudio) for the sound.
  With an NVIDIA card, ffmpeg's `h264_nvenc` encoder is used; otherwise `libx264`.
- **Python 3.8+**.

The server image must match your client's version (`--image`).

## Quick start

```sh
# Which areas does the module have?
./director.py areas ~/modules/mymodule.mod

# Walkthroughs of three of them, 45 seconds each.
./director.py film ~/modules/mymodule.mod \
    --areas townsquare,forestroad,lakeshore \
    --character ~/.local/share/Neverwinter\ Nights/localvault/somechar.bic

# 20 seconds around a door (by tag) and around a point in another area.
./director.py film ~/modules/mymodule.mod \
    --spot townsquare@inn_door --spot lakeshore@120,87 --seconds 20 \
    --character ~/.local/share/Neverwinter\ Nights/localvault/somechar.bic

# A bug report's spot: area tag, position, and the way the player faced (degrees).
./director.py film ~/modules/mymodule.mod \
    --spot FortHope_FrH@213.99,60.32/150 --seconds 30 \
    --character ~/.local/share/Neverwinter\ Nights/localvault/somechar.bic

# The same spot as 8 stills (every 45 degrees) and a contact sheet, without the desktop or a GPU.
./director.py film ~/modules/mymodule.mod \
    --spot FortHope_FrH@213.99,60.32/150 --stills 8 \
    --character ~/.local/share/Neverwinter\ Nights/localvault/somechar.bic

# The inn door opening, and a fireball at a point, 3 seconds into each shot.
./director.py film ~/modules/mymodule.mod \
    --event 'townsquare@inn_door=AssignCommand(OBJECT_SELF, ActionOpenDoor(OBJECT_SELF));' \
    --event 'lakeshore@100,80=ApplyEffectAtLocation(DURATION_TYPE_INSTANT, EffectVisualEffect(VFX_FNF_FIREBALL), GetLocation(OBJECT_SELF));' \
    --seconds 15 --character ~/.local/share/Neverwinter\ Nights/localvault/somechar.bic
```

An event's code is NWScript statements (run as `void main() { ... }`, base game functions only)
or the name of a script the module has. `OBJECT_SELF` is the tagged object, or for a point the
invisible walker standing there. The code is compiled before anything starts, so a mistake stops
at once with the compiler's message.

An AREA is an area's resref or its tag (`director.py areas` lists both). The videos land in
`out/<date-time>/`. Any character works as the cameraman: it is never seen.

Options (`./director.py film --help` for all of them):

| Option | |
|---|---|
| `--character FILE` | the cameraman's character (`.bic`), required |
| `--areas A,B` | walkthroughs of these areas, in this order |
| `--spot AREA@X,Y` / `--spot AREA@TAG` | circle a point, or an object by tag (repeatable); add `/FACING` (degrees, 0 east, 90 north) to start the circle looking that way |
| `--event AREA@X,Y=CODE` / `--event AREA@TAG=CODE` | a spot where CODE runs 3 seconds in (repeatable) |
| `--stills N` | spots as N stills and a contact sheet instead of a video, drawn on the CPU in a virtual display (spots only) |
| `--look LOOK,LOOK` | film every shot once in each of these [looks](#looks) (`none`: as without one) |
| `--looks FILE` | another looks file instead of [looks.json](looks.json) |
| `--sky LOOK=ROW` | film LOOK under `skyboxes.2da` row ROW instead of its own sky (repeatable) |
| `--seconds N` | film each shot for at most N seconds (default 45) |
| `--desktop` | film on your desktop even when VirtualGL is there |
| `--vglrun PATH` | VirtualGL's `vglrun`, if it isn't found by itself ([Filming off screen](#filming-off-screen)) |
| `--run` | the walker runs instead of walking |
| `--out DIR` | where the videos go |
| `--game DIR` | your NWN:EE install, if it isn't found by itself |
| `--user-dir DIR` | your NWN user folder, if it isn't `~/.local/share/Neverwinter Nights` |
| `--hak-dir DIR`, `--tlk-dir DIR` | the module's haks and TLK, if they aren't in your user folder |
| `--client-dir DIR` | the cameraman client's own user folder (default `~/.cache/nwn-director/client`) |
| `--port N` | the server's port on `127.0.0.1` (default 5121; change it if a server of yours already uses it) |
| `--image IMAGE` | the server image, to match your client's version |
| `--keep-server` | leave the server running afterwards (`docker logs nwn-director`) |
| `--script-log` | log every script the server runs, to find what a module's scripts are doing |

## Looks

A look is a time of day, a sky, light and weather for a shot, set before the camera rolls and undone after the cut.
[looks.json](looks.json) has four:

| Look | Time | Sky (`skyboxes.2da`) | What else |
|---|---|---|---|
| `golden-hour` | dusk | 1, Grass_Clear (its dusk) | warm light, orange fog fading in over 8 s, every tile's two main lights pale orange |
| `misty-dawn` | dawn | 3, Desert_Clear (its dawn) | pale grey fog, thick; stock mist placeables over water and fog in hollows near the shot |
| `moonlit` | night | 3, Desert_Clear (its starry night) | dark blue light and fog, tile lights pale dark blue, a blue light 16 m over the spot (on the walker for a walk) as the moon |
| `storm` | day | 2, Grass_Storm | rain, wind, grey fog that darkens over the take, lightning behind the camera every 4 to 9 seconds |

```sh
# A spot as stills in each look, and once as it is.
./director.py film ~/modules/mymodule.mod --spot lakeshore@120,87 --stills 4 \
    --look none,golden-hour,misty-dawn,moonlit,storm \
    --character ~/.local/share/Neverwinter\ Nights/localvault/somechar.bic
```

How it works:

- **A copy of the area per look.** No script can set an area's sun and moon colours, so the module's copy gets one
  more copy of each area for each look it is filmed in (`dir_lk0`, `dir_lk1`, ...), with the look's light in both the
  sun's and the moon's colours, cycling day and night. The time then picks the sky's dawn, day, dusk or night
  picture, and the light stays the look's. The shot is filmed there; its name ends in the look (`<name>-storm`).
- **Script calls** (all in the base game's `nwscript.nss`): `SetTime`, `SetSkyBox`, `SetFogColor`, `SetFogAmount`,
  `SetTileMainLightColor` with `RecomputeStaticLighting`, `SetWeather`, `SetAreaWind`, and effects. Dawn and dusk are
  the module's own hours.
- **Temporary placeables** (mist, fog, the moon's light) are made as the walker is: the stock invisible object
  (`plc_invisobj`) with a `placeables.2da` appearance, tagged `DIR_TMP`. They, the lightning and the weather go after
  the cut.
- **The time is set first**, before anything of the shot is scheduled: `SetTime` runs every delayed command at once.
  It only goes forward, so an earlier hour is the next day's.
- **The cameraman enters the area after the time is set:** a jump of the clock doesn't switch the day and night of the
  area a player is in, nor its sky. Dusk and night show on entering; the dawn sky shows only as the clock runs into
  dawn, so a dawn look sets the clock back to two seconds before dawn as the camera rolls.
- **Fog amount has no fade** in the game: a look sets it before the roll, and fades only the colour.

The looks are data: a look in another file (`--looks FILE`) has these keys, all but `light` optional. Constants are
numbers or their names in `nwscript.nss`.

| Key | |
|---|---|
| `name` | what it is called |
| `light` | the area copy's light: `ambient`, `diffuse` and `fog` colours (`#rrggbb`), `fogAmount`, `shadows`, `fogClip` (the view distance) |
| `time` | `dawn`, `day`, `dusk`, `night`, or an hour |
| `sky` | a `skyboxes.2da` row (`--sky` changes it for a run) |
| `fog` | `color`, `amount` and `fade` (seconds) set by script before the roll |
| `takeFog` | a fog colour to fade to over the take |
| `tileLight` | a `lightcolor.2da` row for every tile's two main lights |
| `weather` | `WEATHER_RAIN`, `WEATHER_SNOW` or `WEATHER_CLEAR` (the default) |
| `wind` | `direction` `[x, y, z]`, `magnitude`, `yaw`, `pitch` (`SetAreaWind`) |
| `mist` | `placeables.2da` rows to place over `water` and in `hollow`s, at most `max`, within `range` m of the spot (a walk: its start); one `hollow` at the spot if there are neither |
| `moon` | a light effect (`VFX_DUR_LIGHT_*`), `height` m over the spot |
| `lightning` | an `effect`, `every` `[min, max]` seconds, `distance` `[min, max]` m behind the camera |

**Skies for now:** the stock rows that come nearest. A module with skies of its own (more rows in its
`skyboxes.2da`) names them with `--sky LOOK=ROW` or in its own looks file.

**Limits:** the looks are made for outdoor areas; in one underground there is no sky to see. Lightning strikes behind
the camera, so its flash and thunder are seen and heard but not its bolt. Mist goes where the area's walkmesh says
water or a hollow is, so water that isn't walkmesh (a picture of water on the ground) gets none.

## GUI shots

The old interface is drawn by the client alone, from the interface textures and fonts it finds (the base game's,
or a hak's that replaces them), so only a real client can show it. `director.py gui` makes a small module of its
own (two rooms of base game interior tiles, no scripts but nwn-director's `dir_gui`) that loads the haks you give
it, starts it on a local server, and logs your client in, drawing on the CPU in a virtual display (Xvfb and
llvmpipe, as for `--stills`). Then it works through a shot list, [gui_shots.json](gui_shots.json):

- **Staging from the server.** Each shot can run NWScript on the player first (`dir_gui` reads it from a small
  campaign database and runs it with `ExecuteScriptChunk`): open a store, a chest, a conversation, the death
  panel, rest, give gold and items, add henchmen to the party bar, jump to the other room for a loading screen.
- **Opening windows from the client** with the game's default keys (`i`, `c`, `b`, `j`, `m`, `p`, Escape) and
  mouse clicks, moves (hover), presses (held down, released elsewhere) and scrolls, through xdotool.
- **Capturing** once the picture has held still (it polls the screen; no fixed waits), or a series while a bar
  fills (resting), or the loading screen while it lasts.

```sh
# The base game's interface, at UI scale 1 and 1.5.
./director.py gui --character ~/.local/share/Neverwinter\ Nights/localvault/somechar.bic --out out/base

# The same with a hak that replaces interface textures (in your hak folder), then what changed.
./director.py gui mytheme --character ~/.local/share/Neverwinter\ Nights/localvault/somechar.bic --out out/theme
./director.py gui-compare out/base out/theme

# A module's haks and TLK (the module itself isn't run).
./director.py gui ~/modules/mymodule.mod --character ~/.local/share/Neverwinter\ Nights/localvault/somechar.bic
```

Out come, per UI scale (`scale-1/`, `scale-1.5/`): `<window>-<state>.png` for each picture, `sheet.png` (all
of them, named), `manifest.json` (each shot: its pictures, what was staged, and anything that failed: a window
that didn't open, a picture that never held still), and `client.log`; and a `manifest.json` over both.
`gui-compare A B` writes, for every picture the two runs share, a score (the share of pixels that changed and the
mean difference) into `report.json`, most changed first, and for each changed one a side-by-side
(`A | B | difference`), so a reviewer only looks at what changed. Pictures only one run has are listed too.

| Option | |
|---|---|
| `SOURCE` | a module (`.mod`) whose haks and TLK to load, or haks (names in the hak folder), comma-separated, first wins |
| `--character FILE` | the character to log in with (`.bic`), required: a spellcaster fills the spellbook |
| `--scale 1,1.5` | UI scales to run at; the screen is 1280x720 at 1, and as much bigger at other scales |
| `--shots FILE` | another shot list |
| `--only A,B` | only these windows of the shot list |
| `--haks`, `--module`, `--tlk` | the same as SOURCE, as options; `--tlk` a custom TLK |
| `--out DIR`, `--game DIR`, `--user-dir DIR`, `--hak-dir DIR`, `--tlk-dir DIR`, `--port N`, `--image IMAGE` | as for `film` |
| `--client-dir DIR` | the GUI client's own user folder (default `~/.cache/nwn-director/gui-client`) |

**Deterministic:** the client's settings are written afresh before every run (no key bindings or window layout
of yours: its folder is its own, and `nwnplayer.ini` and `settings.tml` are deleted first), so every window opens
where the game puts it by default. The room the windows are photographed in has no light (no ambient, sun or
tile lights, black fog), so they are drawn over a dark, still floor: the world moves a fraction of a pixel between
runs (the conversation camera, a camera easing in), which would otherwise show up as change all over the picture.
The time is noon, the camera is fixed looking down, the interface's mouse-over and floating feedback is off, and
each in-game shot starts from the same place with the shot's objects (tagged `DIR_TMP`, their own scripts
cleared, so a chest holds only what the shot put in it) and henchmen removed. Two runs of the same haks differ in
under 0.1% of the pixels of any in-game picture. Positions in the shot list are logical pixels at UI scale 1
from an anchor (centre, an edge or a corner), which is how the game lays out its windows, so the same list works
at every scale.

**What it captures** (the shipped list: 23 windows, about 80 pictures per scale): the main menu (normal, hover,
pressed), the module list, saved games, the multiplayer menu, the options screen; the character list (hover, its
server and player tabs, a record tab, the pregenerated characters); every page of character creation (gender,
race, portrait, class with a hover and a scrolled list, alignment, abilities, packages, skills, feats, appearance
and its colour palette, name and voice); in game the HUD (chat, quickbar and mode buttons hovered, a party of
three, typing in the chat bar, a level-up waiting), inventory (hovered item, second bag tab), an item's examine window, character
record (hover, scroll, skills, feats), spellbook (hover, select, collapse), journal, map, player list, options
(hover, pressed, game options, resolution, keys), radial menu (hover), store, container, conversation (hovered
reply), the rest bar at four fills, the death panel and the loading screen.

**Limits:**

- **Fixed pixel positions.** Clicks follow the game's default layout; a hak that moves or resizes windows (a
  different `.gui` layout, not just textures) can make a click miss. The manifest then says which step didn't
  change the screen.
- **Not reachable without a person or another server:** the server browser's lists (they come from the master
  server), the level-up wizard (`GUI_PANEL_LEVELUP` can't be opened from a script, and the level-up button sits
  in a layout that changes with the character), barter (needs a second player), dungeon master windows, and
  anything that needs a keyboard binding the player changed.
- **The loading screen** is caught at whatever fills the load allows (two or three, with the big second room), and the
  tip under it is chosen at random by the game, so it differs between runs.
- **Pre-game screens** with an animated background (the module list, saved games) are saved once they hold
  still to a looser measure; the background is caught at a different moment each run, so `gui-compare` scores
  them as changed even when the window isn't. Their side-by-side shows which it is.
- **Comparing is slowish:** about a minute for two runs of both scales (the scoring is plain Python).
- **A connected client can't be restarted at once:** the server holds a killed client's CD key for minutes. So
  the character-list shots run in one client that then goes into the game, and it leaves the server (booted by
  the server) before it is stopped.
- **Your CD key is busy while it runs**, as for filming.

**How long:** on a busy 4-core desktop, about 5 minutes per UI scale (79 pictures; 11 minutes for 1 and 1.5),
most of it the client drawing on the CPU and each picture holding still for a second before it is saved.

## Filming off screen

When VirtualGL is there, `film` runs the client in a virtual display of its own (Xvfb, 1920x1080) under VirtualGL's
`vglrun`, which sends its OpenGL to the GPU through EGL (no X server on the card) and copies each frame back into the
virtual display, where ffmpeg records it. Nothing appears on your screen, and your desktop can be locked or in use.
Unlike `--stills`, nothing is turned down: the film settings (high-quality shaders and lighting, grass, shadows,
anti-aliasing) and your screen effects stay on, capped at 60 frames a second. The game's sound goes into a sink of
nwn-director's own (a PipeWire or PulseAudio null sink, removed afterwards, your default output untouched), so it is
recorded but never plays out of your speakers.

VirtualGL needs no root. Either install its package, or unpack its `.deb` into your home, where nwn-director looks
for it:

```sh
# The .deb from https://github.com/VirtualGL/virtualgl/releases (3.1.5 here).
dpkg -x virtualgl_3.1.5_amd64.deb ~/.local/opt/virtualgl
```

It is found as `--vglrun PATH`, else `$NWN_DIRECTOR_VGLRUN`, else `vglrun` on your `PATH`, in `/opt/VirtualGL/bin`
or in `~/.local/opt/virtualgl/opt/VirtualGL/bin`. From an unpacked package, nwn-director tells `vglrun` where its
libraries are. It draws on the first GPU EGL finds; `VGL_DISPLAY=/dev/dri/card1` picks another. Your user needs
access to the card (`/dev/dri/card*`): the logged-in desktop user has it, otherwise the `video` group.

Checked on a GTX 1050 (2 GB, driver 580) with 4 CPU cores, at 1920x1080 with the film settings: a walk over a keep's
torch-lit rooftop drew 40 to 60 frames a second, and a foggy swamp 30 to 45 while the server and another build shared
the CPU. The GPU was about a quarter busy, so the CPU sets the pace. The clip is always 60 frames a second; a frame that
came late is shown twice. Nothing appeared on the desktop.

## Your CD key

The cameraman logs in with your CD key, so the server checks it online like any other login.

- **Don't play on the same key while it films.** Beamdog's master server allows one game per key,
  so the cameraman can't join (nwn-director says so). A second CD key avoids that.
- **Its user folder holds copies of your key files** (`cdkey.ini`, `nwncdkey.ini`,
  `cryptographic_secret`) and settings, in `~/.cache/nwn-director/client` (or `--client-dir`).
  Your haks, TLK and other content folders are linked, not copied. Its logs, saves, temp files and
  caches stay in it, never in your user folder. Delete that folder whenever you like; it is made
  again on the next run.
- **The key is read from `cdkey.ini`** in your user folder, as `YourKey=` under `[NWN1]` (a Steam or GOG
  client can do without it). Without it the client stops at "Could not translate address", which is about the key,
  not the address; nwn-director warns before it starts.
- **Now and then the master server can't check the key** and the server refuses the login ("unable
  to verify your password"). If the cameraman hasn't joined after 2 minutes, nwn-director starts
  the client once more, which usually gets in.

## Known limits

- **The camera passes through trees and other placeables.** Its collision only covers the
  ground and walls, so in a dense forest it sometimes clips through a trunk or a bush.
- **Modules built for NWNX or a database** load, but their scripts that need those stop where
  they need them (the server log says so), and the director doesn't depend on any of it. Some
  of those stopped scripts can crash the plain server outright, though (seen with a script
  aborted inside a delayed command). For such a module, run a server image with the plugins it
  needs (`--image`).
- **The client crashes when it closes** (a game bug, when it tears down the cameraman's
  invisibility effect). nwn-director kills it instead of quitting it, so no crash dialog is left
  on your screen.
- **Character screen:** the Play button is clicked at its position on the default interface
  scale. If you changed the interface scale, the click may miss.

## Tests

```sh
python3 -m unittest discover -v tests
```

They check the plain parts (routes, area lookup, shot options, looks and their area copies, settings, the
contact sheet, how stills are timed; for GUI shots the shot list, positions at each UI scale, waiting for a window to settle, the
generated module, staging and input steps, and comparing two runs) on made-up data, with no game, server or
client; the picture tests need ffmpeg. They run on every push and pull request. Filming itself is tested by hand on a real module.

## Licence

MIT, see [LICENSE](LICENSE).
