# Changelog

Versions follow [Semantic Versioning](https://semver.org/). Before 1.0, a minor version may change options or output names.

## Unreleased

- **`film`: films off screen on the GPU.** With VirtualGL there (installed, or its `.deb` unpacked under
  `~/.local/opt/virtualgl`; `--vglrun` or `$NWN_DIRECTOR_VGLRUN` to point at it), the client draws on the GPU in a
  virtual display of its own, with the film settings, and its sound goes into a sink of nwn-director's own: your
  screen, desktop and speakers are left alone, and the desktop may be locked. Without VirtualGL, or with `--desktop`,
  it films on the desktop as before.
- **`film --look`: looks.** Golden hour, misty dawn, moonlit and storm ([looks.json](looks.json)): each shot is filmed
  once per look, with the look's time, sky, fog, tile lights, weather and temporary mist, lights and lightning, in a
  copy of its area with the look's sun and moon colours. `--sky LOOK=ROW` films a look under another sky, `--looks`
  takes another looks file.
- **`--stills` in a scene that keeps moving** (a look's mist or rain): after 20 seconds an angle is saved once its
  picture changes little, instead of waiting out the 9 minutes for one that never holds still.
- **The invisible walker casts no shadow** in an area that cycles day and night: it is cutscene-invisible now, as the
  cameraman is.
- **`film`: clips keep real time.** On a busy machine the recorder missed frames and the clip came out shorter than
  the shot, playing fast (an 18 s clip of a 20 s circle), so clips of the same shot length fell out of step. A missed
  frame is now shown twice instead.
- **`film`: the cameraman stays out of your user folder.** Its `nwn.ini` was a copy of yours, whose folder aliases sent
  its logs, saves, temp files, caches and databases into your user folder. They now point at its own.
- **Any character works, a pregenerated one too.** The client leaves a local vault character out of its list when one
  of the game's pregenerated characters has the same file name (`barbarian.bic`), so the list stayed empty and Play did
  nothing. The cameraman's copy is now always `nwndirector.bic`.
- **A missing `cdkey.ini` is named.** The client reads its key only from `cdkey.ini` (`[NWN1]`, `YourKey=`); without
  one it stops at "Could not translate address", which says nothing about the key. nwn-director now says so first.

## 0.1.0

The first release.

- **`film`**: copies a module (the original is never changed), runs it on a local plain server in Docker and films each
  shot with your game client as an invisible cameraman, 1080p60 with the game's sound:
  - walkthroughs of whole areas (`--areas`), along a route planned from the module's own area files;
  - spots (`--spot`): a slow circle around a point or an object by tag, starting from a bug report's facing;
  - events (`--event`): a spot where a module script or a line of NWScript runs a few seconds in;
  - stills (`--stills N`): N pictures and a contact sheet per spot, from a client drawing on the CPU in its own
    virtual display, so no GPU is needed.
- **`areas`**: lists a module's areas (resref, tag, size, exits).
- **`gui`**: pictures of the game's old (non-NUI) interface windows in each of their states, to check a hak that
  replaces interface textures or fonts.
- `--version` prints the version.
