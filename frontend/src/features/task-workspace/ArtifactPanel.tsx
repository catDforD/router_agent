import { FileCode2, Loader2 } from "lucide-react";

import type {
  Artifact,
  ArtifactContentResponse,
} from "../../api/router/types";
import type { ArtifactContentState } from "./hooks/useTaskArtifacts";

interface ArtifactPanelProps {
  artifacts: Artifact[];
  loading: boolean;
  error?: string;
  selectedArtifactId: string | null;
  selectedContent?: ArtifactContentState;
  onSelect: (artifactId: string) => void;
}

export function ArtifactPanel({
  artifacts,
  loading,
  error,
  selectedArtifactId,
  selectedContent,
  onSelect,
}: ArtifactPanelProps) {
  return (
    <section className="stack">
      {loading ? (
        <div className="notice">
          <Loader2 size={14} /> Loading artifacts
        </div>
      ) : null}
      {error ? <div className="notice error-box">{error}</div> : null}
      <div className="artifact-list">
        {artifacts.map((artifact) => (
          <button
            aria-selected={artifact.artifact_id === selectedArtifactId}
            className="artifact-row"
            key={artifact.artifact_id}
            type="button"
            onClick={() => onSelect(artifact.artifact_id)}
          >
            <div className="card-heading">
              <strong>{artifact.display_name ?? artifact.name}</strong>
              <span className="artifact-type">{artifact.type}</span>
            </div>
            <p className="small muted">{artifact.summary}</p>
            <div className="inline-list">
              <span className="mini-pill">v{artifact.version}</span>
              <span className="mini-pill">{artifact.status}</span>
              <span className="mini-pill">{artifact.visibility}</span>
              <span className="mini-pill">
                {artifact.storage.mime_type ?? "unknown"}
              </span>
              {artifact.storage.size_bytes ? (
                <span className="mini-pill">{artifact.storage.size_bytes} B</span>
              ) : null}
            </div>
          </button>
        ))}
      </div>

      {selectedContent ? (
        <ArtifactPreview state={selectedContent} />
      ) : artifacts.length ? (
        <div className="empty-state">Select an artifact.</div>
      ) : (
        <div className="empty-state">No artifacts yet.</div>
      )}
    </section>
  );
}

function ArtifactPreview({ state }: { state: ArtifactContentState }) {
  if (state.loading) {
    return <div className="notice">Loading artifact content.</div>;
  }
  if (state.error) {
    return <div className="notice error-box">{state.error}</div>;
  }
  if (!state.response) {
    return null;
  }
  const content = renderContent(state.response, state.parsed);
  return (
    <section className="stack">
      <div className="status-row">
        <span className="status-pill">
          <FileCode2 size={14} />
          {state.response.artifact.type}
        </span>
        <span className="mini-pill">
          {state.response.mime_type ?? "text/plain"}
        </span>
        <span className="mini-pill">
          {state.response.size_bytes ?? state.response.content.length} B
        </span>
      </div>
      <pre className="preview">{content}</pre>
    </section>
  );
}

function renderContent(
  response: ArtifactContentResponse,
  parsed: unknown,
): string {
  if (response.mime_type === "application/json") {
    return JSON.stringify(parsed, null, 2);
  }
  return response.content;
}
