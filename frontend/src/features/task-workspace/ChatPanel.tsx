import { FormEvent, useState } from "react";
import {
  Folder,
  MessageCircle,
  Plus,
  Radio,
  Search,
  Send,
  Settings,
  Wifi,
} from "lucide-react";

import type {
  ProjectContext,
  TaskState,
} from "../../api/router/types";
import type { BackendHealthState } from "./hooks/useTaskState";

export interface TaskListItem {
  taskId: string;
  sessionId?: string;
  title: string;
  status: string;
  phase: string;
  updatedAt: string;
}

interface ChatPanelProps {
  task: TaskState | null;
  taskItems: TaskListItem[];
  health: BackendHealthState;
  loading: boolean;
  error?: string;
  canAppendMessage: boolean;
  onCreateTask: (message: string, context: ProjectContext) => Promise<unknown>;
  onAppendMessage: (message: string) => Promise<unknown>;
  onRefreshHealth: () => Promise<void>;
  onSelectTask: (taskId: string) => void;
}

export function ChatPanel({
  task,
  taskItems,
  health,
  loading,
  error,
  canAppendMessage,
  onCreateTask,
  onAppendMessage,
  onRefreshHealth,
  onSelectTask,
}: ChatPanelProps) {
  const [message, setMessage] = useState("");
  const [followUp, setFollowUp] = useState("");
  const [language, setLanguage] = useState("ST");
  const [platform, setPlatform] = useState("Codesys");

  const openQuestions =
    task?.unresolved_questions.filter((question) => question.status === "open") ?? [];

  const submitTask = async (event: FormEvent) => {
    event.preventDefault();
    if (!message.trim()) {
      return;
    }
    await onCreateTask(message.trim(), {
      target_plc_language: language as ProjectContext["target_plc_language"],
      target_platform: platform,
    });
    setMessage("");
  };

  const submitFollowUp = async (event: FormEvent) => {
    event.preventDefault();
    if (!followUp.trim()) {
      return;
    }
    await onAppendMessage(followUp.trim());
    setFollowUp("");
  };

  return (
    <aside className="task-rail">
      <div className="rail-top">
        <div className="rail-brand">
          <span className="brand-glyph">R</span>
          <div>
            <strong>Router Agent</strong>
            <span>PLC workspace</span>
          </div>
        </div>
        <button
          className="rail-icon-button"
          type="button"
          onClick={onRefreshHealth}
          title="Refresh health"
          aria-label="Refresh health"
        >
          <Wifi size={16} />
        </button>
      </div>

      <nav className="rail-nav" aria-label="Workspace">
        <a href="#tasks">
          <MessageCircle size={16} />
          快速对话
        </a>
        <a href="#search">
          <Search size={16} />
          搜索
        </a>
        <a href="#tasks">
          <Folder size={16} />
          Playground
        </a>
      </nav>

      <section className="rail-section" id="tasks">
        <div className="rail-section-title">
          <span>会话</span>
          <span>{taskItems.length}</span>
        </div>
        <div className="task-list">
          {taskItems.length ? (
            taskItems.map((item) => (
              <button
                className="task-list-item"
                data-active={item.taskId === task?.task_id}
                key={item.taskId}
                type="button"
                onClick={() => onSelectTask(item.taskId)}
              >
                <span title={item.title}>{shortText(item.title, 18)}</span>
                <small>
                  {item.status} · {relativeTime(item.updatedAt)}
                </small>
              </button>
            ))
          ) : (
            <p className="rail-empty">暂无任务</p>
          )}
        </div>
      </section>

      <section className="rail-section grow">
        <div className="rail-section-title">
          <span>新建</span>
          <Plus size={14} />
        </div>
        <form className="rail-form" onSubmit={submitTask}>
          <textarea
            id="task-message"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="输入 PLC 任务目标..."
          />
          <div className="rail-fields">
            <select
              aria-label="Language"
              value={language}
              onChange={(event) => setLanguage(event.target.value)}
            >
              <option value="ST">ST</option>
              <option value="LD">LD</option>
              <option value="FBD">FBD</option>
              <option value="SFC">SFC</option>
              <option value="unknown">unknown</option>
            </select>
            <input
              aria-label="Platform"
              value={platform}
              onChange={(event) => setPlatform(event.target.value)}
            />
          </div>
          <button className="primary-rail-button" type="submit" disabled={loading || !message.trim()}>
            <Send size={15} />
            Create task
          </button>
        </form>
      </section>

      {task ? (
        <section className="rail-section">
          <div className="rail-section-title">
            <span>当前</span>
            <span>{task.phase}</span>
          </div>
          <div className="current-task-card">
            <strong title={task.title ?? task.task_type}>
              {shortText(task.title ?? task.task_type, 18)}
            </strong>
            <p title={task.normalized_goal ?? task.raw_user_request}>
              {shortText(task.normalized_goal ?? task.raw_user_request, 52)}
            </p>
            <div className="rail-chips">
              <span>{task.status}</span>
              <span>repair {task.runtime_limits.repair_rounds}/{task.runtime_limits.max_repair_rounds}</span>
            </div>
          </div>
        </section>
      ) : null}

      {openQuestions.length ? (
        <section className="rail-section">
          <div className="question-card">
            {openQuestions.map((question) => (
              <p key={question.question_id}>
                <strong>{question.required ? "Required" : "Optional"}:</strong>{" "}
                {question.question}
              </p>
            ))}
          </div>
          <form className="rail-form" onSubmit={submitFollowUp}>
            <textarea
              id="follow-up"
              value={followUp}
              onChange={(event) => setFollowUp(event.target.value)}
              placeholder="回复澄清问题..."
            />
            <button
              className="primary-rail-button"
              type="submit"
              disabled={loading || !followUp.trim() || !canAppendMessage}
            >
              <Radio size={15} />
              Send reply
            </button>
          </form>
        </section>
      ) : null}

      <div className="rail-footer">
        <span data-state={health.status} className="health-dot" />
        <span>backend {health.status}</span>
        <button type="button" title="Settings" aria-label="Settings">
          <Settings size={15} />
        </button>
      </div>

      {health.detail ? <div className="rail-error">{health.detail}</div> : null}
      {error ? <div className="rail-error">{error}</div> : null}
    </aside>
  );
}

function relativeTime(value: string): string {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return value;
  }
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) {
    return "刚刚";
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes} 分钟`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours} 小时`;
  }
  return `${Math.floor(hours / 24)} 天`;
}

function shortText(value: string, maxChars: number): string {
  const compact = value.replace(/\s+/g, " ").trim();
  const chars = Array.from(compact);
  if (chars.length <= maxChars) {
    return compact;
  }
  return `${chars.slice(0, maxChars).join("")}...`;
}
