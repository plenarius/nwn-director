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
//                            module's, or dir_ev<n>: the event's code, compiled by director.py)}
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

// Shot n (from 0), or JSON null past the end.
json Dir_Shot(int n)
{
    return JsonArrayGet(JsonParse(GetLocalString(GetModule(), "DIRECTOR_SHOTS")), n);
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

// Camera moves run on the cameraman: SetCameraFacing acts on the calling player's camera.
void Dir_Cam(object oCam, float fDir, float fDist, float fPitch, int nTransition)
{
    AssignCommand(oCam, SetCameraFacing(fDir, fDist, fPitch, nTransition));
}

// Fixed time of day, clear weather, and every encounter on.
void Dir_Set(object oArea)
{
    SetTime(10, 0, 0, 0);
    SetWeather(oArea, WEATHER_CLEAR);
    object o = GetFirstObjectInArea(oArea);
    while (GetIsObjectValid(o))
    {
        if (GetObjectType(o) == OBJECT_TYPE_ENCOUNTER)
            SetEncounterActive(TRUE, o);
        o = GetNextObjectInArea(oArea);
    }
}

// A GFF field of jGff (TemplateToJson / ObjectToJson form) set to a new value of the same type.
json Dir_GffSet(json jGff, string sField, json jValue)
{
    json jField = JsonObjectGet(jGff, sField);
    if (JsonGetType(jField) != JSON_TYPE_OBJECT)
        return jGff;
    return JsonObjectSet(jGff, sField, JsonObjectSet(jField, "value", jValue));
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
    effect e = SupernaturalEffect(EffectLinkEffects(EffectSanctuary(255), EffectCutsceneGhost()));
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
// time), then go on to the next.
void Dir_StillWait(object oCam, object oWalker, int nShot, int nAngle, float fWaited)
{
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
    DelayCommand(0.5, Dir_StillWait(oCam, oWalker, nShot, nAngle, 0.5));
}

// ROLL, then track: always in this order, so a recording never starts after its CUT.
void Dir_Action(object oCam, object oWalker, int nShot, int bWalk, string sName)
{
    Dir_Log("ROLL " + sName);
    if (!bWalk && GetLocalInt(GetModule(), "DIRECTOR_STILLS") > 0)
        Dir_Still(oCam, oWalker, nShot, 0);
    else
        DelayCommand(DIRECTOR_TICK, Dir_Track(oCam, oWalker, nShot, bWalk, DIRECTOR_TICK));
}

void Dir_Roll(object oCam, object oWalker, object oArea, int nShot)
{
    json jShot = Dir_Shot(nShot);
    int bWalk = Dir_ShotString(jShot, "kind") == "walk";
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
    Dir_Set(oArea);
    object oWalker = Dir_Walker(lStart);

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
