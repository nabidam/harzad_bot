// Shapes of the Mini App API (web/app.py). The server is the source of
// truth; these mirror its JSON exactly.

export interface User {
  id: number;
  first_name: string;
  last_name: string;
  username: string;
}

export interface Job {
  id: string;
  state: string; // starting | extracting | downloading | sending | done | error
  url: string;
  title?: string;
  file_name?: string;
  file_url?: string;
  file_id?: string | null;
  file_size_mb?: number;
  error?: string;
}

export interface DayEntry {
  day: string; // YYYY-MM-DD
  label: string; // "Today" | "Yesterday" | date
  count: number;
}

export interface Track {
  track_key: string;
  title: string;
  artist: string;
  album?: string;
  duration?: string | null; // "3:42"
  duration_sec?: number | null;
  has_cover: boolean;
  spotify_url?: string | null;
  source?: string;
  request_count: number;
  added_on?: string | null;
  last_requested?: string | null;
}

export interface LibraryPage {
  view: string;
  sort?: string | null;
  query?: string;
  day?: string | null;
  days?: DayEntry[] | null;
  page: number;
  pages: number;
  total: number;
  tracks: Track[];
}

export interface ResendResult {
  state: "sent" | "queued" | string;
  via?: string;
}

export interface ResendStatus {
  state: "resolving" | "downloading" | "sending" | "sent" | "failed" | string;
  error?: string | null;
}

export interface AuthResult {
  user: User | null;
  anonymous?: boolean;
}

export interface ChatResult {
  reply?: string;
}
