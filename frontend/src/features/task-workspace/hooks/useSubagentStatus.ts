import { useCallback, useEffect, useState } from "react";

import { getSubagentStatus } from "../../../api/router/subagents";
import type { SubagentStatusResponse } from "../../../api/router/types";
import { readableError } from "./useTaskState";

const REFRESH_INTERVAL_MS = 15000;

export interface SubagentStatusState {
  payload: SubagentStatusResponse | null;
  loading: boolean;
  error?: string;
  refresh: () => Promise<SubagentStatusResponse | null>;
}

export function useSubagentStatus(): SubagentStatusState {
  const [payload, setPayload] = useState<SubagentStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>();

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    try {
      const next = await getSubagentStatus();
      setPayload(next);
      return next;
    } catch (err) {
      setError(readableError(err));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    const load = async () => {
      if (!active) {
        return;
      }
      await refresh();
    };
    void load();
    const interval = window.setInterval(() => {
      void load();
    }, REFRESH_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [refresh]);

  return { payload, loading, error, refresh };
}
