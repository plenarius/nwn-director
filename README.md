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

Spots can also be taken as **stills** (`--stills N`): N pictures around the place and a contact
sheet, from a game client drawing on the CPU in a virtual display of its own. That needs no GPU
and leaves your desktop alone, so it can run on a server.

Early days: more kinds of shots are planned.

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
   are never touched) and logs in with the character you give it.
5. **Films each shot:** the camera follows an invisible walker (appearance 298,
   `Invisible_Human_Male`, base game). For a walkthrough it walks the route and the camera
   follows from behind at eye level; for a spot it stands at the place and the camera circles it
   once. The cameraman is
   invisible and ethereal (Greater Sanctuary), so nothing notices or attacks it. Before rolling,
   it steps into every encounter so the area's monsters and wildlife spawn. The time is
   set to 10:00 with clear weather.
6. **Records** each shot with ffmpeg (`<area>.mp4` for a walkthrough, `<area>-<x>-<y>.mp4` or
   `<area>-<tag>.mp4` for a spot, with `-event` added for an event), then closes everything.

   With `--stills N`, the client runs in a virtual display of its own (Xvfb) and draws on the
   CPU, with no sound. For each spot the camera turns through N angles; the server holds each
   one until the picture has turned and been saved (`<name>-00.png` ...), then turns to the
   next. Last comes a contact sheet of them all (`<name>.png`). On 48 cores, 12 stills take
   about 30 seconds after the area loads; on 4 cores, about 6 seconds a still.

## Requirements

- **Linux** with an X11 desktop (recording uses `x11grab`), unlocked while it films. It keeps
  the desktop from idling into the lock screen while it runs (GNOME). For `--stills`, no desktop:
  **Xvfb** and Mesa's software renderer (llvmpipe) instead.
- **NWN:EE** (Steam, GOG or Beamdog), and the module's haks and TLK in your user folder (or
  give their folders).
- **Docker**, to run the server (`ghcr.io/urothis/nwserver`, the community server image). The
  server checks your client's CD key online, so it needs internet access.
- **[neverwinter.nim](https://github.com/niv/neverwinter.nim)** tools on your `PATH`:
  `nwn_erf`, `nwn_gff`, `nwn_script_comp`.
- **ffmpeg**, **xdotool**, **xrandr**, and **pactl** (PipeWire or PulseAudio) for the sound.
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
| `--areas A,B` | walkthroughs of these areas, in this order |
| `--spot AREA@X,Y` / `--spot AREA@TAG` | circle a point, or an object by tag (repeatable); add `/FACING` (degrees, 0 east, 90 north) to start the circle looking that way |
| `--event AREA@X,Y=CODE` / `--event AREA@TAG=CODE` | a spot where CODE runs 3 seconds in (repeatable) |
| `--stills N` | spots as N stills and a contact sheet instead of a video, drawn on the CPU in a virtual display (spots only) |
| `--seconds N` | film each shot for at most N seconds (default 45) |
| `--run` | the walker runs instead of walking |
| `--out DIR` | where the videos go |
| `--game DIR` | your NWN:EE install, if it isn't found by itself |
| `--hak-dir DIR`, `--tlk-dir DIR` | the module's haks and TLK, if they aren't in your user folder |
| `--image IMAGE` | the server image, to match your client's version |
| `--keep-server` | leave the server running afterwards (`docker logs nwn-director`) |
| `--script-log` | log every script the server runs, to find what a module's scripts are doing |

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

## Licence

MIT, see [LICENSE](LICENSE).
