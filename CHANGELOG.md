# Changelog

Versions follow [Semantic Versioning](https://semver.org/). Before 1.0, a minor version may change options or output names.

## Unreleased

- **`film`: films off screen on the GPU.** With VirtualGL there (installed, or its `.deb` unpacked under
  `~/.local/opt/virtualgl`; `--vglrun` or `$NWN_DIRECTOR_VGLRUN` to point at it), the client draws on the GPU in a
  virtual display of its own, with the film settings, and its sound goes into a sink of nwn-director's own: your
  screen, desktop and speakers are left alone, and the desktop may be locked. Without VirtualGL, or with `--desktop`,
  it films on the desktop as before.

- **`film`: clips keep real time.** On a busy machine the recorder missed frames and the clip came out shorter than
  the shot, playing fast (an 18 s clip of a 20 s circle), so clips of the same shot length fell out of step. A missed
  frame is now shown twice instead.
- **`film`: the cameraman stays out of your user folder.** Its `nwn.ini` was a copy of yours, whose folder aliases sent
  its logs, saves, temp files, caches and databases into your user folder. They now point at its own.

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
