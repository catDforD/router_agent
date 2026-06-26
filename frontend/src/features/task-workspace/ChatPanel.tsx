import { FormEvent, useState } from "react";
import {
  Folder,
  MessageCircle,
  Plus,
  Radio,
  Search,
  Settings,
  Trash2,
  Wifi,
} from "lucide-react";

import type { TaskState } from "../../api/router/types";
import type { BackendHealthState } from "./hooks/useTaskState";

export type RailView = "quick" | "search" | "playground";

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
  activeView: RailView;
  onAppendMessage: (message: string) => Promise<unknown>;
  onRefreshHealth: () => Promise<void>;
  onSelectTask: (taskId: string) => void;
  onNewTask: () => void;
  onDeleteTask: (taskId: string) => Promise<void>;
  onViewChange: (view: RailView) => void;
  onDraftMessage: (message: string) => void;
  onFocusComposer: () => void;
}

export function ChatPanel({
  task,
  taskItems,
  health,
  loading,
  error,
  canAppendMessage,
  activeView,
  onAppendMessage,
  onRefreshHealth,
  onSelectTask,
  onNewTask,
  onDeleteTask,
  onViewChange,
  onDraftMessage,
  onFocusComposer,
}: ChatPanelProps) {
  const [followUp, setFollowUp] = useState("");
  const [searchQuery, setSearchQuery] = useState("");

  const openQuestions =
    task?.unresolved_questions.filter((question) => question.status === "open") ?? [];
  const visibleTaskItems =
    activeView === "search" && searchQuery.trim()
      ? taskItems.filter((item) =>
          `${item.title} ${item.status} ${item.phase}`
            .toLowerCase()
            .includes(searchQuery.trim().toLowerCase()),
        )
      : taskItems;

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
        <div className="rail-top-actions">
          <button
            className="rail-icon-button primary"
            type="button"
            onClick={onNewTask}
            title="新任务"
            aria-label="新任务"
          >
            <Plus size={16} />
          </button>
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
      </div>

      <nav className="rail-nav" aria-label="Workspace">
        <button
          data-active={activeView === "quick"}
          type="button"
          onClick={() => onViewChange("quick")}
        >
          <MessageCircle size={16} />
          快速对话
        </button>
        <button
          data-active={activeView === "search"}
          type="button"
          onClick={() => onViewChange("search")}
        >
          <Search size={16} />
          搜索
        </button>
        <button
          data-active={activeView === "playground"}
          type="button"
          onClick={() => {
            onViewChange("playground");
            onFocusComposer();
          }}
        >
          <Folder size={16} />
          Playground
        </button>
      </nav>

      <section className="rail-section" id="tasks">
        <div className="rail-section-title">
          <span>{railTitle(activeView)}</span>
          <span>{activeView === "quick" ? quickPrompts.length : visibleTaskItems.length}</span>
        </div>
        {activeView === "quick" ? (
          <div className="quick-list">
            {quickPrompts.map((prompt) => (
              <button
                className="quick-prompt"
                key={prompt.title}
                type="button"
                onClick={() => {
                  onDraftMessage(prompt.message);
                  onViewChange("playground");
                }}
              >
                <strong>{prompt.title}</strong>
                <span>{prompt.description}</span>
              </button>
            ))}
          </div>
        ) : null}
        {activeView === "search" ? (
          <div className="rail-search">
            <input
              aria-label="Search tasks"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="搜索任务..."
            />
          </div>
        ) : null}
        {activeView !== "quick" ? (
          <div className="task-list">
            {visibleTaskItems.length ? (
              visibleTaskItems.map((item) => (
                <div
                  className="task-list-row"
                  data-active={item.taskId === task?.task_id}
                  key={item.taskId}
                >
                  <button
                    className="task-list-item"
                    type="button"
                    onClick={() => onSelectTask(item.taskId)}
                  >
                    <span title={item.title}>{shortText(item.title, 18)}</span>
                    <small>
                      {item.status} · {relativeTime(item.updatedAt)}
                    </small>
                  </button>
                  <button
                    className="task-delete-button"
                    type="button"
                    title="删除会话"
                    aria-label={`删除会话：${item.title}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      if (window.confirm("确定删除这个会话吗？")) {
                        void onDeleteTask(item.taskId);
                      }
                    }}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))
            ) : (
              <p className="rail-empty">
                {activeView === "search" ? "没有匹配任务" : "暂无任务"}
              </p>
            )}
          </div>
        ) : null}
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
              <span>
                repair {task.runtime_limits.repair_rounds}/{task.runtime_limits.max_repair_rounds}
              </span>
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

const quickPrompts = [
  {
    title: "电机启停",
    description: "启动/停止/运行指示，输出 ST 代码",
    message:
      "帮我写一个简单电机启停PLC控制逻辑，包含启动按钮、停止按钮、运行指示，输出ST代码和测试结果。",
  },
  {
    title: "安全联锁",
    description: "急停、故障锁存、人工复位",
    message:
      "帮我设计一个带急停、故障锁存和人工复位的PLC电机控制逻辑，说明关键设计点并输出ST代码。",
  },
  {
    title: "概念说明",
    description: "只解释设计，不生成完整代码",
    message:
      "请用简洁语言说明PLC电机启停控制中自保持、停止优先和故障复位分别应该怎么设计，不需要生成完整代码。",
  },
];

function railTitle(view: RailView): string {
  if (view === "quick") {
    return "快速对话";
  }
  if (view === "search") {
    return "搜索";
  }
  return "任务";
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
