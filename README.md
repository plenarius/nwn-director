# nwn-director

Scripted camera capture for Neverwinter Nights: Enhanced Edition modules.

Give it a module and what to film. It runs the module on a local server, logs your game client
in as an invisible cameraman, and films each shot: one video per shot, 1080p60 with the game's
sound.

- **Walkthroughs** of whole areas, to cut into a trailer or a montage.
- **Spots**: a slow circle around one place (a point, or an object by tag), to see what is
  really there when triaging a bug report.

Early days: more kinds of shots are planned (events: a script run while the camera rolls).

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
   `<area>-<tag>.mp4` for a spot), then closes everything.

## Requirements

- **Linux** with an X11 desktop (recording uses `x11grab`), unlocked while it films. It keeps
  the desktop from idling into the lock screen while it runs (GNOME).
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
```

The videos land in `out/<date-time>/`. Any character works as the cameraman: it is never seen.

Options (`./director.py film --help` for all of them):

| Option | |
|---|---|
| `--areas A,B` | walkthroughs of these areas, in this order |
| `--spot AREA@X,Y` / `--spot AREA@TAG` | circle a point, or an object by tag (repeatable) |
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
