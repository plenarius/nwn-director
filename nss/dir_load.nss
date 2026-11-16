// dir_load: nwn-director's module load script (see README.md).
//
// director.py makes a copy of a module with this script in two places: as its OnModuleLoad (which
// still runs the module's own load script, module variable DIRECTOR_ONLOAD), and as the spawn
// script of an invisible anchor creature in the first area to film. Whichever runs first starts
// the director. The anchor is there for modules whose framework takes over the module's events
// before they load (the Core Framework does): it spawns while the module loads, and nothing has
// hooked it. Once started, the director waits for the cameraman: the game client that renders the
// walkthroughs and gets recorded. Once it is in, every area in
// DIRECTOR_AREAS is filmed in turn: an invisible walker (appearance.2da row 298,
// Invisible_Human_Male, base game) walks the area's route in DIRECTOR_ROUTES and the camera
// follows it from behind at eye level.
//
// Module variables (set by director.py):
//   DIRECTOR_ONLOAD  string  the module's own OnModuleLoad script
//   DIRECTOR_AREAS   string  area resrefs, comma-separated, in filming order
//   DIRECTOR_ROUTES  string  JSON {area: {"stops": [[x, y], ...], "encounters": [[x, y], ...]}}
//   DIRECTOR_SECONDS float   seconds to film each area at most
//   DIRECTOR_RUN     int     TRUE: the walker runs
//
// Lines in the server log tell director.py what to do:
//   "DIRECTOR: ROLL <area>"  start recording <area>.mp4
//   "DIRECTOR: CUT"          stop recording
//   "DIRECTOR: END"          all areas done

const int DIRECTOR_WALKER_APPEARANCE = 298; // appearance.2da Invisible_Human_Male
const float DIRECTOR_TICK = 1.0;            // seconds between camera updates
const float DIRECTOR_CAM_DIST = 7.0;
const float DIRECTOR_CAM_PITCH = 78.0;      // 1 looks straight down, 89 is level
const float DIRECTOR_LOAD_TIME = 6.0;       // seconds for the client to load an area

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

// The nth (from 0) resref in DIRECTOR_AREAS, or "" past the end.
string Dir_AreaName(int n)
{
    string sList = GetLocalString(GetModule(), "DIRECTOR_AREAS") + ",";
    int nStart = 0;
    int nComma = FindSubString(sList, ",");
    while (n > 0 && nComma >= 0)
    {
        nStart = nComma + 1;
        nComma = FindSubString(sList, ",", nStart);
        n--;
    }
    if (nComma < 0)
        return "";
    return GetSubString(sList, nStart, nComma - nStart);
}

json Dir_Route(string sArea, string sList)
{
    json jRoutes = JsonParse(GetLocalString(GetModule(), "DIRECTOR_ROUTES"));
    return JsonObjectGet(JsonObjectGet(jRoutes, sArea), sList);
}

// (fX, fY) on the ground, if it is somewhere a creature can walk.
int Dir_Walkable(object oArea, float fX, float fY)
{
    int nMaterial = GetSurfaceMaterial(Location(oArea, Vector(fX, fY, 0.0), 0.0));
    return nMaterial > 0 && Get2DAString("surfacemat", "Walk", nMaterial) == "1";
}

// Point n (from 0) of sArea's sList ("stops" or "encounters"), on the ground. A point is [x, y],
// or [x, y, x2, y2] with a second choice (the two sides of a door). One off the walkmesh moves to
// the nearest walkable spot within 5 m.
location Dir_Point(object oArea, string sArea, string sList, int n)
{
    json jPoint = JsonArrayGet(Dir_Route(sArea, sList), n);
    float fX = JsonGetFloat(JsonArrayGet(jPoint, 0));
    float fY = JsonGetFloat(JsonArrayGet(jPoint, 1));
    if (JsonGetLength(jPoint) >= 4 && !Dir_Walkable(oArea, fX, fY))
    {
        fX = JsonGetFloat(JsonArrayGet(jPoint, 2));
        fY = JsonGetFloat(JsonArrayGet(jPoint, 3));
    }
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

// Camera moves run on the cameraman: SetCameraFacing acts on the calling player's camera.
void Dir_Cam(object oCam, float fDir, int nTransition)
{
    AssignCommand(oCam, SetCameraFacing(fDir, DIRECTOR_CAM_DIST, DIRECTOR_CAM_PITCH, nTransition));
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

void Dir_DoArea(object oCam, int nArea);

void Dir_Track(object oCam, object oWalker, int nArea, float fElapsed)
{
    Dir_Cam(oCam, GetFacing(oWalker), CAMERA_TRANSITION_TYPE_SLOW);
    Dir_Keep(oCam, oWalker);
    if (GetLocalInt(oWalker, "DIRECTOR_DONE") || fElapsed >= GetLocalFloat(GetModule(), "DIRECTOR_SECONDS"))
    {
        Dir_Log("CUT");
        AttachCamera(oCam, OBJECT_INVALID);
        DestroyObject(oWalker, 1.0);
        DelayCommand(2.0, Dir_DoArea(oCam, nArea + 1));
        return;
    }
    DelayCommand(DIRECTOR_TICK, Dir_Track(oCam, oWalker, nArea, fElapsed + DIRECTOR_TICK));
}

// ROLL, then follow the walker: always in this order, so a recording never starts after its CUT.
void Dir_Action(object oCam, object oWalker, int nArea, string sArea)
{
    Dir_Log("ROLL " + sArea);
    DelayCommand(DIRECTOR_TICK, Dir_Track(oCam, oWalker, nArea, DIRECTOR_TICK));
}

void Dir_Roll(object oCam, object oWalker, object oArea, int nArea, int nStops)
{
    string sArea = Dir_AreaName(nArea);
    SetCameraLimits(oCam, 1.0, 89.0, 1.0, 40.0);
    SetCameraFlags(oCam, CAMERA_FLAG_ENABLE_COLLISION | CAMERA_FLAG_DISABLE_SHAKE | CAMERA_FLAG_DISABLE_SCROLL
        | CAMERA_FLAG_DISABLE_TURN | CAMERA_FLAG_DISABLE_TILT | CAMERA_FLAG_DISABLE_ZOOM);
    AttachCamera(oCam, oWalker, FALSE);

    // Forced moves: a stop with no path to it (a door, a spot inside a building) doesn't end the
    // walk; after a while the walker is jumped there instead.
    int bRun = GetLocalInt(GetModule(), "DIRECTOR_RUN");
    location lFrom = GetLocation(oWalker);
    int i;
    for (i = 1; i < nStops; i++)
    {
        location lTo = Dir_Point(oArea, sArea, "stops", i);
        float fTimeout = GetDistanceBetweenLocations(lFrom, lTo) / (bRun ? 3.0 : 1.4) + 10.0;
        AssignCommand(oWalker, ActionForceMoveToLocation(lTo, bRun, fTimeout));
        lFrom = lTo;
    }
    AssignCommand(oWalker, ActionDoCommand(SetLocalInt(oWalker, "DIRECTOR_DONE", TRUE)));

    // Face the first leg, so the take opens behind the walker.
    AssignCommand(oWalker, SetFacingPoint(GetPositionFromLocation(Dir_Point(oArea, sArea, "stops", 1))));
    DelayCommand(0.5, Dir_Cam(oCam, GetFacing(oWalker), CAMERA_TRANSITION_TYPE_SNAP));
    DelayCommand(1.5, Dir_Action(oCam, oWalker, nArea, sArea));
}

void Dir_DoArea(object oCam, int nArea)
{
    string sName = Dir_AreaName(nArea);
    if (sName == "")
    {
        Dir_Log("END");
        return;
    }
    object oArea = Dir_Area(sName);
    if (!GetIsObjectValid(oArea))
    {
        Dir_Log("skip " + sName + ": not in the module");
        Dir_DoArea(oCam, nArea + 1);
        return;
    }
    int nStops = JsonGetLength(Dir_Route(sName, "stops"));
    if (nStops < 2)
    {
        Dir_Log("skip " + sName + ": nothing to walk between");
        Dir_DoArea(oCam, nArea + 1);
        return;
    }
    Dir_Set(oArea);
    location lStart = Dir_Point(oArea, sName, "stops", 0);
    object oWalker = Dir_Walker(lStart);

    // The cameraman goes too; changing area detaches the camera, so it is attached once the area
    // has loaded. Meanwhile it steps into every encounter so they spawn (a PC entering sets one
    // off), then back to the start.
    AssignCommand(oCam, ClearAllActions(TRUE));
    AssignCommand(oCam, JumpToLocation(lStart));
    int nEnc = JsonGetLength(Dir_Route(sName, "encounters"));
    int i;
    for (i = 0; i < nEnc; i++)
        DelayCommand(DIRECTOR_LOAD_TIME + 1.0 * i,
            AssignCommand(oCam, JumpToLocation(Dir_Point(oArea, sName, "encounters", i))));
    DelayCommand(DIRECTOR_LOAD_TIME + 1.0 * nEnc, AssignCommand(oCam, JumpToLocation(lStart)));
    DelayCommand(DIRECTOR_LOAD_TIME + 4.0 + 1.0 * nEnc, Dir_Roll(oCam, oWalker, oArea, nArea, nStops));
}

void Dir_Wait(int nTries)
{
    object oPC = GetFirstPC();
    if (GetIsObjectValid(oPC) && GetIsObjectValid(GetArea(oPC)))
    {
        Dir_Log("cameraman in: " + GetName(oPC));
        Dir_Hide(oPC);
        DelayCommand(4.0, Dir_DoArea(oPC, 0));
        return;
    }
    if (nTries % 30 == 0)
        Dir_Log("waiting for the cameraman");
    DelayCommand(1.0, Dir_Wait(nTries + 1));
}

// Start the director once (run on the module, so it outlives the anchor). The first delayed
// command only runs once the module has finished loading and the server is running.
void Dir_Start()
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
        AssignCommand(oModule, Dir_Start());
        DestroyObject(OBJECT_SELF);
        return;
    }
    // The module's load: start (if the anchor hasn't), then the module's own load script, last,
    // so that if it aborts (a module built for NWNX or a database it doesn't have here) filming
    // still starts.
    Dir_Start();
    string sOnLoad = GetLocalString(oModule, "DIRECTOR_ONLOAD");
    if (sOnLoad != "")
        ExecuteScript(sOnLoad, oModule);
}
