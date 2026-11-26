// dir_load: nwn-director's script (see README.md).
//
// director.py makes a copy of a module with this script in two places: as its OnModuleLoad (which
// still runs the module's own load script, module variable DIRECTOR_ONLOAD), and as the spawn
// script of an invisible anchor creature in the first area to film. Whichever runs first starts
// the director. The anchor is there for modules whose framework takes over the module's events
// before they load (the Core Framework does): it spawns while the module loads, and nothing has
// hooked it.
//
// Once started, the director waits for the cameraman (the game client that renders the shots and
// gets recorded), then films every shot in DIRECTOR_SHOTS in turn. The camera follows an
// invisible walker (appearance.2da row 298, Invisible_Human_Male, base game):
//   walk  the walker walks the area's route and the camera follows it from behind at eye level
//   spot  the walker stands at a place (a point, or an object by tag) and the camera circles it;
//         with an "event", that runs DIRECTOR_EVENT_DELAY seconds in, so the clip shows before
//         and after; with a "facing", the circle starts looking that way (as a player standing
//         there faced, from a bug report)
//
// Module variables (set by director.py):
//   DIRECTOR_ONLOAD  string  the module's own OnModuleLoad script
//   DIRECTOR_SHOTS   string  JSON list of shots, each {"name", "area", "kind": "walk" | "spot",
//                            "encounters": [[x, y], ...], and for a walk "stops": [[x, y], ...],
//                            for a spot "at": [x, y] or "tag": "TAG", maybe "facing": degrees
//                            (0 east, 90 north) and maybe "event": the script to run (the
//                            module's, or dir_ev<n>: the event's code, compiled by director.py),
//                            maybe "look": a look from looks.json as director.py resolves it
//                            (its area is then director.py's copy with the look's light)}
//   DIRECTOR_SECONDS float   seconds to film each shot at most
//   DIRECTOR_RUN     int     TRUE: the walker runs
//   DIRECTOR_STILLS  int     stills instead of video: spots turn through this many angles, each
//                            held until director.py has saved its still (it counts them in the
//                            campaign database "director", table stills)
//
// A point is [x, y], or [x, y, x2, y2] with a second choice (the two sides of a door).
//
// Lines in the server log tell director.py what to do:
//   "DIRECTOR: ROLL <name>"  start recording <name>.mp4 (stills: start shot <name>)
//   "DIRECTOR: STILL <n>"    stills: the camera is at angle n; save it, then count it
//   "DIRECTOR: CUT"          stop recording
//   "DIRECTOR: END"          all shots done
//   "DIRECTOR: event ..."    an event ran
//   "DIRECTOR: look ..."     a look was set

const int DIRECTOR_WALKER_APPEARANCE = 298; // appearance.2da Invisible_Human_Male
const float DIRECTOR_TICK = 1.0;            // seconds between camera updates
const float DIRECTOR_LOAD_TIME = 6.0;       // seconds for the client to load an area
// Camera: pitch 1 looks straight down, 89 is level.
const float DIRECTOR_WALK_DIST = 7.0;
const float DIRECTOR_WALK_PITCH = 78.0;
const float DIRECTOR_SPOT_DIST = 9.0;
const float DIRECTOR_SPOT_PITCH = 68.0;
const float DIRECTOR_EVENT_DELAY = 3.0;     // seconds into a spot before its event runs

void Dir_Log(string sMsg)
{
    WriteTimestampedLogEntry("DIRECTOR: " + sMsg);
}

object Dir_Area(string sResRef)
{
    object oArea = GetFirstArea();
    while (GetIsObjectValid(oArea))
    {
        if (GetResRef(oArea) == sResRef)
            return oArea;
        oArea = GetNextArea();
    }
    return OBJECT_INVALID;
}

// Shot n (from 0), or JSON null past the end. The list is parsed once (Dir_Shot runs every tick).
json Dir_Shot(int n)
{
    object oModule = GetModule();
    json jShots = GetLocalJson(oModule, "DIRECTOR_SHOTS");
    if (JsonGetType(jShots) != JSON_TYPE_ARRAY)
    {
        jShots = JsonParse(GetLocalString(oModule, "DIRECTOR_SHOTS"));
        SetLocalJson(oModule, "DIRECTOR_SHOTS", jShots);
    }
    return JsonArrayGet(jShots, n);
}

string Dir_ShotString(json jShot, string sKey)
{
    return JsonGetString(JsonObjectGet(jShot, sKey));
}

// (fX, fY) on the ground, if it is somewhere a creature can walk.
int Dir_Walkable(object oArea, float fX, float fY)
{
    int nMaterial = GetSurfaceMaterial(Location(oArea, Vector(fX, fY, 0.0), 0.0));
    return nMaterial > 0 && Get2DAString("surfacemat", "Walk", nMaterial) == "1";
}

// (fX, fY) on the ground, or the nearest walkable spot within 5 m if it is off the walkmesh (a
// door's frame, inside a building).
location Dir_Ground(object oArea, float fX, float fY)
{
    if (!Dir_Walkable(oArea, fX, fY))
    {
        float fR, fA;
        int bFound = FALSE;
        for (fR = 1.0; fR <= 5.0 && !bFound; fR += 1.0)
            for (fA = 0.0; fA < 360.0 && !bFound; fA += 30.0)
                if (Dir_Walkable(oArea, fX + fR * cos(fA), fY + fR * sin(fA)))
                {
                    fX += fR * cos(fA);
                    fY += fR * sin(fA);
                    bFound = TRUE;
                }
    }
    location l = Location(oArea, Vector(fX, fY, 0.0), 0.0);
    return Location(oArea, Vector(fX, fY, GetGroundHeight(l)), 0.0);
}

// A point ([x, y] or [x, y, x2, y2]) on the ground.
location Dir_Point(object oArea, json jPoint)
{
    float fX = JsonGetFloat(JsonArrayGet(jPoint, 0));
    float fY = JsonGetFloat(JsonArrayGet(jPoint, 1));
    if (JsonGetLength(jPoint) >= 4 && !Dir_Walkable(oArea, fX, fY))
    {
        fX = JsonGetFloat(JsonArrayGet(jPoint, 2));
        fY = JsonGetFloat(JsonArrayGet(jPoint, 3));
    }
    return Dir_Ground(oArea, fX, fY);
}

// The object tagged sTag in oArea, or OBJECT_INVALID.
object Dir_Tagged(object oArea, string sTag)
{
    int n = 0;
    object o = GetObjectByTag(sTag, n);
    while (GetIsObjectValid(o))
    {
        if (GetArea(o) == oArea)
            return o;
        o = GetObjectByTag(sTag, ++n);
    }
    return OBJECT_INVALID;
}

// Where a shot starts: a walk's first stop, a spot's point or tagged object.
location Dir_Start(object oArea, json jShot)
{
    if (Dir_ShotString(jShot, "kind") == "walk")
        return Dir_Point(oArea, JsonArrayGet(JsonObjectGet(jShot, "stops"), 0));
    string sTag = Dir_ShotString(jShot, "tag");
    if (sTag != "")
    {
        object o = Dir_Tagged(oArea, sTag);
        if (!GetIsObjectValid(o))
            return Location(OBJECT_INVALID, Vector(), 0.0);
        vector v = GetPosition(o);
        return Dir_Ground(oArea, v.x, v.y);
    }
    return Dir_Point(oArea, JsonObjectGet(jShot, "at"));
}

// Camera moves run on the cameraman: SetCameraFacing acts on the calling player's camera. The direction is kept
// for a look's lightning, which strikes behind the camera.
void Dir_Cam(object oCam, float fDir, float fDist, float fPitch, int nTransition)
{
    SetLocalFloat(GetModule(), "DIRECTOR_CAM_DIR", fDir);
    AssignCommand(oCam, SetCameraFacing(fDir, fDist, fPitch, nTransition));
}

// A GFF field of jGff (TemplateToJson / ObjectToJson form) set to a new value of the same type.
json Dir_GffSet(json jGff, string sField, json jValue)
{
    json jField = JsonObjectGet(jGff, sField);
    if (JsonGetType(jField) != JSON_TYPE_OBJECT)
        return jGff;
    return JsonObjectSet(jGff, sField, JsonObjectSet(jField, "value", jValue));
}

// ---------------------------------------------------------------- looks

// jLook's sKey as an int, or nDefault if it has none.
int Dir_LookInt(json jLook, string sKey, int nDefault)
{
    json j = JsonObjectGet(jLook, sKey);
    return JsonGetType(j) == JSON_TYPE_INTEGER ? JsonGetInt(j) : nDefault;
}

float Dir_LookFloat(json jLook, string sKey)
{
    return JsonGetFloat(JsonObjectGet(jLook, sKey));
}

// A temporary placeable for a look (mist, fog, a light), made as Dir_Walker makes the walker: the stock invisible
// object, with a placeables.2da appearance (-1: stays invisible). Tagged DIR_TMP, destroyed after the cut.
object Dir_Temp(location lLoc, int nAppearance)
{
    json j = TemplateToJson("plc_invisobj", RESTYPE_UTP);
    if (nAppearance >= 0)
        j = Dir_GffSet(j, "Appearance", JsonInt(nAppearance));
    j = Dir_GffSet(j, "Tag", JsonString("DIR_TMP"));
    j = Dir_GffSet(j, "Plot", JsonInt(1));
    object o = JsonToObject(j, lLoc);
    SetName(o, " ");
    SetObjectUiDiscoveryMask(o, OBJECT_UI_DISCOVERY_NONE);
    SetObjectVisibleDistance(o, 100.0);
    return o;
}

// (fX, fY) is in a hollow: the ground there is lower than all around it, 5, 10 and 15 m out.
int Dir_Hollow(object oArea, float fX, float fY, float fZ)
{
    float fR, fA;
    for (fR = 5.0; fR <= 15.0; fR += 5.0)
        for (fA = 0.0; fA < 360.0; fA += 45.0)
        {
            location l = Location(oArea, Vector(fX + fR * cos(fA), fY + fR * sin(fA), 0.0), 0.0);
            if (GetSurfaceMaterial(l) <= 0 || GetGroundHeight(l) < fZ + 0.3)
                return FALSE;
        }
    return TRUE;
}

// A look's mist near lAt: a "water" placeable over water (surfacemat.2da IsWater) and a "hollow" one in hollows, at
// most "max", none within 8 m of another. If the place has neither, one "hollow" at lAt, so a misty look is misty.
void Dir_Mist(object oArea, json jMist, location lAt)
{
    int nWater = Dir_LookInt(jMist, "water", -1);
    int nHollow = Dir_LookInt(jMist, "hollow", -1);
    int nMax = Dir_LookInt(jMist, "max", 12);
    float fRange = Dir_LookFloat(jMist, "range");
    float fWidth = 10.0 * GetAreaSize(AREA_WIDTH, oArea) - 2.5;
    float fHeight = 10.0 * GetAreaSize(AREA_HEIGHT, oArea) - 2.5;
    vector vAt = GetPositionFromLocation(lAt);
    float fX0 = vAt.x - fRange < 2.5 ? 2.5 : vAt.x - fRange;
    float fY0 = vAt.y - fRange < 2.5 ? 2.5 : vAt.y - fRange;
    json jTaken = JsonArray();
    int nPlaced = 0;
    float fX, fY;
    for (fY = fY0; fY <= fHeight && fY <= vAt.y + fRange && nPlaced < nMax; fY += 5.0)
        for (fX = fX0; fX <= fWidth && fX <= vAt.x + fRange && nPlaced < nMax; fX += 5.0)
        {
            location l = Location(oArea, Vector(fX, fY, 0.0), 0.0);
            int nMaterial = GetSurfaceMaterial(l);
            if (nMaterial <= 0)
                continue;
            float fZ = GetGroundHeight(l);
            int nRow = -1;
            if (Get2DAString("surfacemat", "IsWater", nMaterial) == "1")
                nRow = nWater;
            else if (nHollow >= 0 && Dir_Hollow(oArea, fX, fY, fZ))
                nRow = nHollow;
            if (nRow < 0)
                continue;
            int i, bNear = FALSE;
            for (i = 0; i < JsonGetLength(jTaken) && !bNear; i += 2)
                bNear = fabs(JsonGetFloat(JsonArrayGet(jTaken, i)) - fX) < 8.0
                    && fabs(JsonGetFloat(JsonArrayGet(jTaken, i + 1)) - fY) < 8.0;
            if (bNear)
                continue;
            Dir_Temp(Location(oArea, Vector(fX, fY, fZ), 0.0), nRow);
            jTaken = JsonArrayInsert(JsonArrayInsert(jTaken, JsonFloat(fX)), JsonFloat(fY));
            nPlaced++;
        }
    if (nPlaced == 0 && nHollow >= 0)
    {
        Dir_Temp(lAt, nHollow);
        nPlaced = 1;
    }
    Dir_Log("look mist: " + IntToString(nPlaced) + " placeables");
}

// Every tile's two main lights in one lightcolor.2da colour.
void Dir_TileLights(object oArea, int nColor)
{
    int nWidth = GetAreaSize(AREA_WIDTH, oArea);
    int nHeight = GetAreaSize(AREA_HEIGHT, oArea);
    int x, y;
    for (y = 0; y < nHeight; y++)
        for (x = 0; x < nWidth; x++)
            SetTileMainLightColor(Location(oArea, Vector(10.0 * x + 5.0, 10.0 * y + 5.0, 0.0), 0.0), nColor, nColor);
    RecomputeStaticLighting(oArea);
}

// A shot's look before the camera rolls (the time and weather are Dir_Set's): sky, fog, tile lights, wind, mist and
// the moon. The sun's and moon's own colours can't be set by script: the area is director.py's copy with them.
void Dir_Look(object oArea, json jLook, location lAt, int bWalk)
{
    int nSky = Dir_LookInt(jLook, "sky", -1);
    if (nSky >= 0)
        SetSkyBox(nSky, oArea);
    json jFog = JsonObjectGet(jLook, "fog");
    if (JsonGetType(jFog) == JSON_TYPE_OBJECT)
    {
        // The amount has no fade: it is set now, and only the colour fades.
        SetFogColor(FOG_TYPE_ALL, Dir_LookInt(jFog, "color", 0), oArea, Dir_LookFloat(jFog, "fade"));
        int nAmount = Dir_LookInt(jFog, "amount", -1);
        if (nAmount >= 0)
            SetFogAmount(FOG_TYPE_ALL, nAmount, oArea);
    }
    int nTileLight = Dir_LookInt(jLook, "tileLight", -1);
    if (nTileLight >= 0)
        Dir_TileLights(oArea, nTileLight);
    json jWind = JsonObjectGet(jLook, "wind");
    if (JsonGetType(jWind) == JSON_TYPE_OBJECT)
        SetAreaWind(oArea, Vector(Dir_LookFloat(jWind, "x"), Dir_LookFloat(jWind, "y"), Dir_LookFloat(jWind, "z")),
            Dir_LookFloat(jWind, "magnitude"), Dir_LookFloat(jWind, "yaw"), Dir_LookFloat(jWind, "pitch"));
    json jMist = JsonObjectGet(jLook, "mist");
    if (JsonGetType(jMist) == JSON_TYPE_OBJECT)
        Dir_Mist(oArea, jMist, lAt);
    // The moon: a light high over a spot (a walk's is on the walker, Dir_LookWalker).
    json jMoon = JsonObjectGet(jLook, "moon");
    if (JsonGetType(jMoon) == JSON_TYPE_OBJECT && !bWalk)
    {
        vector v = GetPositionFromLocation(lAt);
        object oMoon = Dir_Temp(Location(oArea, Vector(v.x, v.y, v.z + Dir_LookFloat(jMoon, "height")), 0.0), -1);
        ApplyEffectToObject(DURATION_TYPE_PERMANENT, EffectVisualEffect(Dir_LookInt(jMoon, "light", 0)), oMoon);
    }
    Dir_Log("look " + JsonGetString(JsonObjectGet(jLook, "id")) + " set");
}

// A walk's moon goes with the walker.
void Dir_LookWalker(object oWalker, json jLook)
{
    json jMoon = JsonObjectGet(jLook, "moon");
    if (JsonGetType(jMoon) == JSON_TYPE_OBJECT)
        ApplyEffectToObject(DURATION_TYPE_PERMANENT, EffectVisualEffect(Dir_LookInt(jMoon, "light", 0)), oWalker);
}

// A look's lightning while shot nShot rolls: a strike behind the camera, out of shot (its flash and thunder are
// what's seen and heard), every few seconds.
void Dir_Lightning(object oWalker, int nShot, json jLightning)
{
    if (GetLocalInt(GetModule(), "DIRECTOR_ROLLING") != nShot + 1 || !GetIsObjectValid(oWalker))
        return;
    float fDir = GetLocalFloat(GetModule(), "DIRECTOR_CAM_DIR") + 180.0 + IntToFloat(Random(121) - 60);
    float fDistMin = Dir_LookFloat(jLightning, "distMin");
    float fDist = fDistMin + (Dir_LookFloat(jLightning, "distMax") - fDistMin) * IntToFloat(Random(101)) / 100.0;
    vector v = GetPosition(oWalker);
    location l = Location(GetArea(oWalker), Vector(v.x + fDist * cos(fDir), v.y + fDist * sin(fDir), v.z), 0.0);
    ApplyEffectAtLocation(DURATION_TYPE_INSTANT, EffectVisualEffect(Dir_LookInt(jLightning, "effect", 0)), l);
    float fEveryMin = Dir_LookFloat(jLightning, "everyMin");
    float fNext = fEveryMin + (Dir_LookFloat(jLightning, "everyMax") - fEveryMin) * IntToFloat(Random(101)) / 100.0;
    DelayCommand(fNext, Dir_Lightning(oWalker, nShot, jLightning));
}

// As the camera rolls: a look's fog colour fades over the take, and its lightning starts.
void Dir_LookRoll(object oWalker, int nShot, json jLook)
{
    SetLocalInt(GetModule(), "DIRECTOR_ROLLING", nShot + 1);
    if (JsonGetType(jLook) != JSON_TYPE_OBJECT)
        return;
    int nTakeFog = Dir_LookInt(jLook, "takeFog", -1);
    if (nTakeFog >= 0)
        SetFogColor(FOG_TYPE_ALL, nTakeFog, GetArea(oWalker), GetLocalFloat(GetModule(), "DIRECTOR_SECONDS"));
    json jLightning = JsonObjectGet(jLook, "lightning");
    if (JsonGetType(jLightning) == JSON_TYPE_OBJECT)
        DelayCommand(1.0, Dir_Lightning(oWalker, nShot, jLightning));
}

// After the cut: the look's temporary placeables go, its lightning stops, and the area's weather is its own again.
// The rest (sky, fog, tile lights, wind) stays in the copy, which only shots in the same look use, and they set it.
void Dir_Unlook(object oArea)
{
    DeleteLocalInt(GetModule(), "DIRECTOR_ROLLING");
    int n = 0;
    object o = GetObjectByTag("DIR_TMP", n);
    while (GetIsObjectValid(o))
    {
        if (GetArea(o) == oArea)
            DestroyObject(o);
        o = GetObjectByTag("DIR_TMP", ++n);
    }
    SetWeather(oArea, WEATHER_USE_AREA_SETTINGS);
}

// The shot's time (10:00, or its look's) and weather (clear, or its look's), every encounter on, then its look.
// SetTime comes first, before anything of the shot is scheduled: it runs every pending DelayCommand at once.
void Dir_Set(object oArea, json jShot, location lAt)
{
    json jLook = JsonObjectGet(jShot, "look");
    SetTime(Dir_LookInt(jLook, "hour", 10), 0, 0, 0);
    SetWeather(oArea, Dir_LookInt(jLook, "weather", WEATHER_CLEAR));
    object o = GetFirstObjectInArea(oArea);
    while (GetIsObjectValid(o))
    {
        if (GetObjectType(o) == OBJECT_TYPE_ENCOUNTER)
            SetEncounterActive(TRUE, o);
        o = GetNextObjectInArea(oArea);
    }
    if (JsonGetType(jLook) == JSON_TYPE_OBJECT)
        Dir_Look(oArea, jLook, lAt, Dir_ShotString(jShot, "kind") == "walk");
}

// The invisible walker the camera follows. Made from the stock commoner blueprint with every
// script blanked before it exists, so none of the module's scripts (a custom spawn script, waypoint
// walking, AI) ever run on it. It can't be hurt and monsters leave it alone.
object Dir_Walker(location lLoc)
{
    json j = TemplateToJson("nw_commale", RESTYPE_UTC);
    string sScripts = "ScriptAttacked,ScriptDamaged,ScriptDeath,ScriptDialogue,ScriptDisturbed,ScriptEndRound,"
        + "ScriptHeartbeat,ScriptOnBlocked,ScriptOnNotice,ScriptRested,ScriptSpawn,ScriptSpellAt,ScriptUserDefine,";
    int nStart = 0;
    int nComma = FindSubString(sScripts, ",");
    while (nComma >= 0)
    {
        j = Dir_GffSet(j, GetSubString(sScripts, nStart, nComma - nStart), JsonString(""));
        nStart = nComma + 1;
        nComma = FindSubString(sScripts, ",", nStart);
    }
    j = Dir_GffSet(j, "Appearance_Type", JsonInt(DIRECTOR_WALKER_APPEARANCE));
    j = Dir_GffSet(j, "Tag", JsonString("DIRECTOR_WALKER"));
    j = Dir_GffSet(j, "Conversation", JsonString(""));
    object oWalker = JsonToObject(j, lLoc);

    SetName(oWalker, " ");
    SetPlotFlag(oWalker, TRUE);
    SetObjectUiDiscoveryMask(oWalker, OBJECT_UI_DISCOVERY_NONE);
    SetObjectVisibleDistance(oWalker, 100.0);
    // Cutscene invisibility too: in an area that cycles day and night, the invisible model still casts a shadow.
    effect e = SupernaturalEffect(EffectLinkEffects(EffectSanctuary(255), EffectLinkEffects(EffectCutsceneGhost(),
        EffectVisualEffect(VFX_DUR_CUTSCENE_INVISIBILITY))));
    ApplyEffectToObject(DURATION_TYPE_PERMANENT, e, oWalker);
    return oWalker;
}

// The cameraman stays out of shot (invisible, no collision), unnoticed (ethereal: Greater
// Sanctuary, so nothing attacks it), and in cutscene mode (no interface).
void Dir_Hide(object oCam)
{
    effect eHide = SupernaturalEffect(EffectLinkEffects(EffectEthereal(), EffectLinkEffects(
        EffectVisualEffect(VFX_DUR_CUTSCENE_INVISIBILITY), EffectCutsceneGhost())));
    ApplyEffectToObject(DURATION_TYPE_PERMANENT, eHide, oCam);
    SetCutsceneMode(oCam, TRUE);
}

// Keep the cameraman near the walker (AttachCamera needs it perceived). A move to a location, not
// to the walker: moving to or following an object makes it the action target, and the client rings it.
void Dir_Keep(object oCam, object oWalker)
{
    if (GetDistanceBetween(oCam, oWalker) > 4.0)
    {
        AssignCommand(oCam, ClearAllActions());
        AssignCommand(oCam, ActionMoveToLocation(GetLocation(oWalker), TRUE));
    }
}

void Dir_DoShot(object oCam, int nShot);

// A spot's event (a script: the module's, or the event's code compiled by director.py), run on
// the tagged object, or on the walker standing at the point.
void Dir_Event(object oWalker, object oArea, json jShot)
{
    string sScript = Dir_ShotString(jShot, "event");
    object oSelf = oWalker;
    string sTag = Dir_ShotString(jShot, "tag");
    if (sTag != "")
        oSelf = Dir_Tagged(oArea, sTag);
    ExecuteScript(sScript, oSelf);
    Dir_Log("event ran " + sScript);
}

// End a shot: free the camera and go on to the next one.
void Dir_Cut(object oCam, object oWalker, int nShot)
{
    Dir_Log("CUT");
    AttachCamera(oCam, OBJECT_INVALID);
    Dir_Unlook(GetArea(oWalker));
    DestroyObject(oWalker, 1.0);
    DelayCommand(2.0, Dir_DoShot(oCam, nShot + 1));
}

// A spot's camera direction after fElapsed of fSeconds: one circle, from the shot's facing.
float Dir_SpotDir(json jShot, float fElapsed, float fSeconds)
{
    float fDir = JsonGetFloat(JsonObjectGet(jShot, "facing")) + 360.0 * fElapsed / fSeconds;
    while (fDir >= 360.0)
        fDir -= 360.0;
    return fDir;
}

// Every tick while rolling: walks follow the walker from behind, spots circle it once over the shot.
void Dir_Track(object oCam, object oWalker, int nShot, int bWalk, float fElapsed)
{
    float fSeconds = GetLocalFloat(GetModule(), "DIRECTOR_SECONDS");
    if (bWalk)
        Dir_Cam(oCam, GetFacing(oWalker), DIRECTOR_WALK_DIST, DIRECTOR_WALK_PITCH, CAMERA_TRANSITION_TYPE_SLOW);
    else
        Dir_Cam(oCam, Dir_SpotDir(Dir_Shot(nShot), fElapsed, fSeconds), DIRECTOR_SPOT_DIST, DIRECTOR_SPOT_PITCH, CAMERA_TRANSITION_TYPE_MEDIUM);
    Dir_Keep(oCam, oWalker);
    if (GetLocalInt(oWalker, "DIRECTOR_DONE") || fElapsed >= fSeconds)
    {
        Dir_Cut(oCam, oWalker, nShot);
        return;
    }
    DelayCommand(DIRECTOR_TICK, Dir_Track(oCam, oWalker, nShot, bWalk, fElapsed + DIRECTOR_TICK));
}

// How many stills director.py has saved, from the campaign database it writes (-1: none there).
int Dir_StillsSaved()
{
    sqlquery q = SqlPrepareQueryCampaign("director", "SELECT n FROM stills");
    return SqlStep(q) ? SqlGetInt(q, 0) : -1;
}

void Dir_Still(object oCam, object oWalker, int nShot, int nAngle);

// Hold the angle until director.py has saved its still (rendering may be far slower than real
// time), then go on to the next. The camera is set again every time: a slow client that is still
// loading the area may put it back where it was.
void Dir_StillWait(object oCam, object oWalker, int nShot, int nAngle, float fWaited)
{
    float fStills = IntToFloat(GetLocalInt(GetModule(), "DIRECTOR_STILLS"));
    Dir_Cam(oCam, Dir_SpotDir(Dir_Shot(nShot), IntToFloat(nAngle), fStills), DIRECTOR_SPOT_DIST,
        DIRECTOR_SPOT_PITCH, CAMERA_TRANSITION_TYPE_SNAP);
    if (Dir_StillsSaved() >= GetLocalInt(GetModule(), "DIRECTOR_STILLS_ASKED"))
    {
        Dir_Still(oCam, oWalker, nShot, nAngle + 1);
        return;
    }
    if (fWaited >= 600.0)
    {
        Dir_Log("skip the rest of " + Dir_ShotString(Dir_Shot(nShot), "name") + ": no still saved in 10 minutes");
        Dir_Cut(oCam, oWalker, nShot);
        return;
    }
    DelayCommand(0.5, Dir_StillWait(oCam, oWalker, nShot, nAngle, fWaited + 0.5));
}

// Turn the camera to angle nAngle of the shot's DIRECTOR_STILLS and ask for its still.
void Dir_Still(object oCam, object oWalker, int nShot, int nAngle)
{
    object oModule = GetModule();
    int nStills = GetLocalInt(oModule, "DIRECTOR_STILLS");
    if (nAngle >= nStills)
    {
        Dir_Cut(oCam, oWalker, nShot);
        return;
    }
    Dir_Cam(oCam, Dir_SpotDir(Dir_Shot(nShot), IntToFloat(nAngle), IntToFloat(nStills)), DIRECTOR_SPOT_DIST,
        DIRECTOR_SPOT_PITCH, CAMERA_TRANSITION_TYPE_SNAP);
    Dir_Keep(oCam, oWalker);
    SetLocalInt(oModule, "DIRECTOR_STILLS_ASKED", GetLocalInt(oModule, "DIRECTOR_STILLS_ASKED") + 1);
    Dir_Log("STILL " + IntToString(nAngle));
    // The first angle's black lifts a little after it is asked for, so that director.py, reading the log, sees the
    // black even on a fast client.
    if (nAngle == 0)
        DelayCommand(2.0, StopFade(oCam));
    DelayCommand(0.5, Dir_StillWait(oCam, oWalker, nShot, nAngle, 0.5));
}

// ROLL, then track: always in this order, so a recording never starts after its CUT.
void Dir_Action(object oCam, object oWalker, int nShot, int bWalk, string sName)
{
    Dir_Log("ROLL " + sName);
    Dir_LookRoll(oWalker, nShot, JsonObjectGet(Dir_Shot(nShot), "look"));
    if (!bWalk && GetLocalInt(GetModule(), "DIRECTOR_STILLS") > 0)
        Dir_Still(oCam, oWalker, nShot, 0);
    else
        DelayCommand(DIRECTOR_TICK, Dir_Track(oCam, oWalker, nShot, bWalk, DIRECTOR_TICK));
}

void Dir_Roll(object oCam, object oWalker, object oArea, int nShot)
{
    json jShot = Dir_Shot(nShot);
    int bWalk = Dir_ShotString(jShot, "kind") == "walk";
    // Stills: black until the camera is set (Dir_Still lifts it). A slow client can take seconds to move the
    // camera into place; the client does what the server says in order, so the first picture after the black has
    // it in place, and director.py waits for that.
    if (!bWalk && GetLocalInt(GetModule(), "DIRECTOR_STILLS") > 0)
        BlackScreen(oCam);
    SetCameraLimits(oCam, 1.0, 89.0, 1.0, 40.0);
    SetCameraFlags(oCam, CAMERA_FLAG_ENABLE_COLLISION | CAMERA_FLAG_DISABLE_SHAKE | CAMERA_FLAG_DISABLE_SCROLL
        | CAMERA_FLAG_DISABLE_TURN | CAMERA_FLAG_DISABLE_TILT | CAMERA_FLAG_DISABLE_ZOOM);
    AttachCamera(oCam, oWalker, FALSE);

    if (bWalk)
    {
        // Forced moves: a stop with no path to it doesn't end the walk; after a while the walker
        // is jumped there instead.
        json jStops = JsonObjectGet(jShot, "stops");
        int bRun = GetLocalInt(GetModule(), "DIRECTOR_RUN");
        location lFrom = GetLocation(oWalker);
        int i;
        for (i = 1; i < JsonGetLength(jStops); i++)
        {
            location lTo = Dir_Point(oArea, JsonArrayGet(jStops, i));
            float fTimeout = GetDistanceBetweenLocations(lFrom, lTo) / (bRun ? 3.0 : 1.4) + 10.0;
            AssignCommand(oWalker, ActionForceMoveToLocation(lTo, bRun, fTimeout));
            lFrom = lTo;
        }
        AssignCommand(oWalker, ActionDoCommand(SetLocalInt(oWalker, "DIRECTOR_DONE", TRUE)));
        // Face the first leg, so the take opens behind the walker.
        AssignCommand(oWalker, SetFacingPoint(GetPositionFromLocation(Dir_Point(oArea, JsonArrayGet(jStops, 1)))));
        DelayCommand(0.5, Dir_Cam(oCam, GetFacing(oWalker), DIRECTOR_WALK_DIST, DIRECTOR_WALK_PITCH,
            CAMERA_TRANSITION_TYPE_SNAP));
    }
    else
    {
        DelayCommand(0.5, Dir_Cam(oCam, Dir_SpotDir(jShot, 0.0, 1.0), DIRECTOR_SPOT_DIST, DIRECTOR_SPOT_PITCH, CAMERA_TRANSITION_TYPE_SNAP));
        if (Dir_ShotString(jShot, "event") != "")
            DelayCommand(1.5 + DIRECTOR_EVENT_DELAY, Dir_Event(oWalker, oArea, jShot));
    }
    DelayCommand(1.5, Dir_Action(oCam, oWalker, nShot, bWalk, Dir_ShotString(jShot, "name")));
}

void Dir_DoShot(object oCam, int nShot)
{
    json jShot = Dir_Shot(nShot);
    if (JsonGetType(jShot) != JSON_TYPE_OBJECT)
    {
        Dir_Log("END");
        return;
    }
    string sName = Dir_ShotString(jShot, "name");
    string sArea = Dir_ShotString(jShot, "area");
    object oArea = Dir_Area(sArea);
    if (!GetIsObjectValid(oArea))
    {
        Dir_Log("skip " + sName + ": no area " + sArea + " in the module");
        Dir_DoShot(oCam, nShot + 1);
        return;
    }
    if (Dir_ShotString(jShot, "kind") == "walk" && JsonGetLength(JsonObjectGet(jShot, "stops")) < 2)
    {
        Dir_Log("skip " + sName + ": nothing to walk between");
        Dir_DoShot(oCam, nShot + 1);
        return;
    }
    location lStart = Dir_Start(oArea, jShot);
    if (!GetIsObjectValid(GetAreaFromLocation(lStart)))
    {
        Dir_Log("skip " + sName + ": no object tagged " + Dir_ShotString(jShot, "tag") + " in " + sArea);
        Dir_DoShot(oCam, nShot + 1);
        return;
    }
    Dir_Set(oArea, jShot, lStart);
    object oWalker = Dir_Walker(lStart);
    Dir_LookWalker(oWalker, JsonObjectGet(jShot, "look"));

    // The cameraman goes too; changing area detaches the camera, so it is attached once the area
    // has loaded. Meanwhile it steps into every encounter so they spawn (a PC entering sets one
    // off), then back to the start.
    AssignCommand(oCam, ClearAllActions(TRUE));
    AssignCommand(oCam, JumpToLocation(lStart));
    json jEnc = JsonObjectGet(jShot, "encounters");
    int nEnc = JsonGetLength(jEnc);
    int i;
    for (i = 0; i < nEnc; i++)
        DelayCommand(DIRECTOR_LOAD_TIME + 1.0 * i,
            AssignCommand(oCam, JumpToLocation(Dir_Point(oArea, JsonArrayGet(jEnc, i)))));
    DelayCommand(DIRECTOR_LOAD_TIME + 1.0 * nEnc, AssignCommand(oCam, JumpToLocation(lStart)));
    DelayCommand(DIRECTOR_LOAD_TIME + 4.0 + 1.0 * nEnc, Dir_Roll(oCam, oWalker, oArea, nShot));
}

void Dir_Wait(int nTries)
{
    object oPC = GetFirstPC();
    if (GetIsObjectValid(oPC) && GetIsObjectValid(GetArea(oPC)))
    {
        Dir_Log("cameraman in: " + GetName(oPC));
        Dir_Hide(oPC);
        DelayCommand(4.0, Dir_DoShot(oPC, 0));
        return;
    }
    if (nTries % 30 == 0)
        Dir_Log("waiting for the cameraman");
    DelayCommand(1.0, Dir_Wait(nTries + 1));
}

// Start the director once (run on the module, so it outlives the anchor). The first delayed
// command only runs once the module has finished loading and the server is running.
void Dir_Begin()
{
    object oModule = GetModule();
    if (GetLocalInt(oModule, "DIRECTOR_STARTED"))
        return;
    SetLocalInt(oModule, "DIRECTOR_STARTED", TRUE);
    DelayCommand(1.0, Dir_Log("module loaded"));
    DelayCommand(2.0, Dir_Wait(0));
}

void main()
{
    object oModule = GetModule();
    if (OBJECT_SELF != oModule)
    {
        // The anchor's spawn.
        AssignCommand(oModule, Dir_Begin());
        DestroyObject(OBJECT_SELF);
        return;
    }
    // The module's load: start (if the anchor hasn't), then the module's own load script, last,
    // so that if it aborts (a module built for NWNX or a database it doesn't have here) filming
    // still starts.
    Dir_Begin();
    string sOnLoad = GetLocalString(oModule, "DIRECTOR_ONLOAD");
    if (sOnLoad != "")
        ExecuteScript(sOnLoad, oModule);
}
