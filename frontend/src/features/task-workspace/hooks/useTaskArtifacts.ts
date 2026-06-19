import { useCallback, useMemo, useState } from "react";

import {
  listTaskArtifacts,
  parseArtifactContent,
  readArtifactContent,
} from "../../../api/router/artifacts";
import type {
  Artifact,
  ArtifactContentResponse,
} from "../../../api/router/types";
import { readableError } from "./useTaskState";

export interface ArtifactContentState {
  loading: boolean;
  error?: string;
  response?: ArtifactContentResponse;
  parsed?: unknown;
}

export function useTaskArtifacts(taskId: string | null) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [contentById, setContentById] = useState<Record<string, ArtifactContentState>>(
    {},
  );

  const refreshArtifacts = useCallback(async () => {
    if (!taskId) {
      setArtifacts([]);
      return [];
    }
    setLoading(true);
    setError(undefined);
    try {
      const payload = await listTaskArtifacts(taskId);
      setArtifacts(payload.artifacts);
      return payload.artifacts;
    } catch (err) {
      setError(readableError(err));
      return [];
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  const loadContent = useCallback(async (artifactId: string) => {
    setSelectedArtifactId(artifactId);
    setContentById((current) => ({
      ...current,
      [artifactId]: {
        ...(current[artifactId] ?? {}),
        loading: true,
        error: undefined,
      },
    }));
    try {
      const response = await readArtifactContent(artifactId);
      let parsed: unknown = response.content;
      try {
        parsed = parseArtifactContent(response);
      } catch {
        parsed = response.content;
      }
      setContentById((current) => ({
        ...current,
        [artifactId]: { loading: false, response, parsed },
      }));
      return response;
    } catch (err) {
      setContentById((current) => ({
        ...current,
        [artifactId]: { loading: false, error: readableError(err) },
      }));
      throw err;
    }
  }, []);

  const selectedArtifact = artifacts.find(
    (artifact) => artifact.artifact_id === selectedArtifactId,
  );
  const selectedContent = selectedArtifactId
    ? contentById[selectedArtifactId]
    : undefined;

  return useMemo(
    () => ({
      artifacts,
      loading,
      error,
      selectedArtifactId,
      selectedArtifact,
      selectedContent,
      contentById,
      setSelectedArtifactId,
      refreshArtifacts,
      loadContent,
    }),
    [
      artifacts,
      loading,
      error,
      selectedArtifactId,
      selectedArtifact,
      selectedContent,
      contentById,
      refreshArtifacts,
      loadContent,
    ],
  );
}
