import { useEffect, useMemo, useRef, useState } from "react";

import {
  isTerminalEvent,
  openTaskEventStream,
  type StreamState,
} from "../../../api/router/events";
import type { RouterEvent } from "../../../api/router/types";

export function useTaskEvents(taskId: string | null, enabled = true) {
  const [events, setEvents] = useState<RouterEvent[]>([]);
  const [streamState, setStreamState] = useState<StreamState>("idle");
  const [latestSeq, setLatestSeq] = useState(0);
  const [streamError, setStreamError] = useState<string | undefined>();
  const latestSeqRef = useRef(0);
  const reconnectTimerRef = useRef<number | undefined>(undefined);
  const terminalCloseTimerRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (terminalCloseTimerRef.current !== undefined) {
      window.clearTimeout(terminalCloseTimerRef.current);
      terminalCloseTimerRef.current = undefined;
    }
    latestSeqRef.current = 0;
    setLatestSeq(0);
    setEvents([]);
    setStreamError(undefined);
  }, [taskId]);

  useEffect(() => {
    if (!taskId || !enabled) {
      setStreamState("idle");
      return undefined;
    }

    let closed = false;
    let stream: ReturnType<typeof openTaskEventStream> | undefined;

    const connect = (state: StreamState) => {
      setStreamState(state);
      stream = openTaskEventStream(taskId, {
        afterSeq: latestSeqRef.current,
        onOpen: () => {
          if (!closed) {
            setStreamState("connected");
            setStreamError(undefined);
          }
        },
        onEvent: (event) => {
          if (closed) {
            return;
          }
          latestSeqRef.current = Math.max(latestSeqRef.current, event.seq);
          setLatestSeq(latestSeqRef.current);
          setEvents((current) => appendUniqueEvent(current, event));
          if (
            isTerminalEvent(event) &&
            terminalCloseTimerRef.current === undefined
          ) {
            terminalCloseTimerRef.current = window.setTimeout(() => {
              if (!closed) {
                setStreamState("closed");
                stream?.close();
              }
              terminalCloseTimerRef.current = undefined;
            }, 1600);
          }
        },
        onError: () => {
          if (closed) {
            return;
          }
          setStreamState("reconnecting");
          setStreamError("Event stream disconnected. Reconnecting.");
          stream?.close();
          reconnectTimerRef.current = window.setTimeout(() => {
            if (!closed) {
              connect("reconnecting");
            }
          }, 1200);
        },
      });
    };

    connect("connecting");

    return () => {
      closed = true;
      if (reconnectTimerRef.current !== undefined) {
        window.clearTimeout(reconnectTimerRef.current);
      }
      if (terminalCloseTimerRef.current !== undefined) {
        window.clearTimeout(terminalCloseTimerRef.current);
        terminalCloseTimerRef.current = undefined;
      }
      stream?.close();
    };
  }, [taskId, enabled]);

  return useMemo(
    () => ({
      events,
      streamState,
      latestSeq,
      streamError,
      latestEvent: events.at(-1) ?? null,
      resetEvents: () => setEvents([]),
    }),
    [events, streamState, latestSeq, streamError],
  );
}

function appendUniqueEvent(
  current: RouterEvent[],
  incoming: RouterEvent,
): RouterEvent[] {
  if (
    current.some(
      (event) =>
        event.event_id === incoming.event_id ||
        (event.seq === incoming.seq && event.type === incoming.type),
    )
  ) {
    return current;
  }
  return [...current, incoming].sort((left, right) => left.seq - right.seq);
}
