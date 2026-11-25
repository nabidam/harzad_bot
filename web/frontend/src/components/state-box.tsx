import { COPY } from "../copy";
import { Icon } from "../icons";

export type StateKind =
  | "empty"
  | "often-empty"
  | "no-results"
  | "unavailable"
  | "no-auth"
  | "day-empty";

export interface StateBoxProps {
  kind: StateKind;
  extra?: string;
  onRetry?: () => void;
}

/** v1 showMusicState, 1:1. */
export function StateBox({ kind, extra, onRetry }: StateBoxProps) {
  let iconName = "music";
  let tone = "soft";
  let t = "";
  let desc = "";
  let retry = false;
  switch (kind) {
    case "empty":
      t = COPY.empty;
      desc = COPY.emptyDesc;
      break;
    case "often-empty":
      iconName = "clock";
      t = COPY.oftenEmpty;
      desc = COPY.oftenEmptyDesc;
      break;
    case "no-results":
      iconName = "search";
      t = COPY.noResultsTitle;
      desc = "No tracks matched \"" + (extra || "") + '". Try another title or artist name.';
      break;
    case "unavailable":
      iconName = "cloud-off";
      tone = "warn";
      t = COPY.unavailable;
      desc = COPY.unavailableDesc;
      retry = true;
      break;
    case "no-auth":
      iconName = "lock";
      t = COPY.noAuth;
      desc = COPY.noAuthDesc;
      break;
    case "day-empty":
      iconName = "calendar";
      t = COPY.dayEmpty;
      desc = COPY.dayEmptyDesc;
      break;
  }
  return (
    <div className="statebox" id="music-state">
      <div className={"state-ico " + tone}>
        <Icon name={iconName} />
      </div>
      <div className="state-t">{t}</div>
      <div className="state-d">{desc}</div>
      {retry && (
        <button className="btn primary" onClick={onRetry}>Try again</button>
      )}
    </div>
  );
}
