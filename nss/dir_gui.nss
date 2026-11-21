// dir_gui: nwn-director's script for GUI shots (see README.md, "GUI shots").
//
// director.py gui makes a small module with this script as its OnModuleLoad and OnClientEnter.
// The player who joins is the one whose windows get photographed. director.py stages each shot by
// writing NWScript into the campaign database "director" (table steps: n, code); this script runs
// each new step on the player with ExecuteScriptChunk, in order, and says so in the server log:
//   "DIRECTOR: module loaded"     the server is ready for the client
//   "DIRECTOR: player in"         the player is in the first area
//   "DIRECTOR: DONE <n>"          step n ran
//   "DIRECTOR: FAIL <n> <error>"  step n did not compile (the compiler's message)
//
// A step's code is the body of a main() run on the player (OBJECT_SELF), with "x0_i0_position"
// included for placing things around them.

const float DIRECTOR_POLL = 0.25; // seconds between looks at the steps table

void Dir_Log(string sMsg)
{
    WriteTimestampedLogEntry("DIRECTOR: " + sMsg);
}

// Run every step after nDone that director.py has written, then look again.
void Dir_Poll(object oPC, int nDone)
{
    if (!GetIsObjectValid(oPC))
        return;
    sqlquery q = SqlPrepareQueryCampaign("director", "SELECT n, code FROM steps WHERE n > @n ORDER BY n");
    SqlBindInt(q, "@n", nDone);
    while (SqlStep(q))
    {
        int n = SqlGetInt(q, 0);
        string sError = ExecuteScriptChunk("#include \"x0_i0_position\"\nvoid main()\n{\n" + SqlGetString(q, 1)
            + "\n}\n", oPC, FALSE);
        if (sError == "")
            Dir_Log("DONE " + IntToString(n));
        else
            Dir_Log("FAIL " + IntToString(n) + " " + sError);
        nDone = n;
    }
    DelayCommand(DIRECTOR_POLL, Dir_Poll(oPC, nDone));
}

// Wait until the player is in an area (OnClientEnter runs before that).
void Dir_WaitIn(object oPC, int nTries)
{
    if (!GetIsObjectValid(oPC))
        return;
    if (GetIsObjectValid(GetArea(oPC)))
    {
        Dir_Log("player in");
        Dir_Poll(oPC, 0);
        return;
    }
    if (nTries < 600)
        DelayCommand(0.5, Dir_WaitIn(oPC, nTries + 1));
}

void main()
{
    object oModule = GetModule();
    if (OBJECT_SELF == oModule && GetEnteringObject() == OBJECT_INVALID)
    {
        // The module's load: a fixed time of day, so the light and the minimap look the same every run.
        SetTime(12, 0, 0, 0);
        DelayCommand(1.0, Dir_Log("module loaded"));
        return;
    }
    object oPC = GetEnteringObject();
    if (GetIsPC(oPC))
        DelayCommand(1.0, Dir_WaitIn(oPC, 0));
}
