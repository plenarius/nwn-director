# Changelog

Versions follow [Semantic Versioning](https://semver.org/). Before 1.0, a minor version may change options or output names.

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
