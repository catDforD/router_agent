FUNCTION_BLOCK FB_FaultDominates
VAR_INPUT
    enable : BOOL;
    fault : BOOL;
END_VAR
VAR_OUTPUT
    run : BOOL;
END_VAR

run := enable AND NOT fault;

//#ASSERT (run = (enable AND NOT fault)) : assert_run_respects_fault
END_FUNCTION_BLOCK
