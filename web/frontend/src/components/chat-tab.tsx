export interface UiMsg {
  role: "user" | "bot";
  content: string;
  inHistory: boolean; // false = transient bubble (thinking / error text)
  thinking?: boolean;
}

export interface ChatTabProps {
  messages: UiMsg[];
}

/** v1 #messages, 1:1 (textContent in v1 → React text nodes are equally safe). */
export function ChatTab({ messages }: ChatTabProps) {
  return (
    <div className="msgs" id="messages">
      {messages.map((m, i) =>
        m.thinking ? (
          <div className="msg bot" key={i}>
            <span className="typing"><i></i><i></i><i></i></span>
          </div>
        ) : (
          <div className={"msg " + m.role} key={i}>{m.content}</div>
        ),
      )}
    </div>
  );
}
