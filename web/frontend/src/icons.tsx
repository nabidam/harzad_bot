export const ICONS: Record<string, string[]> = {

  "music": ["M3 17a3 3 0 1 0 6 0a3 3 0 0 0 -6 0","M13 17a3 3 0 1 0 6 0a3 3 0 0 0 -6 0","M9 17v-13h10v13","M9 8h10"],
  "circle-check": ["M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0","M9 12l2 2l4 -4"],
  "external-link": ["M12 6h-6a2 2 0 0 0 -2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-6","M11 13l9 -9","M15 4h5v5"],
  "refresh": ["M20 11a8.1 8.1 0 0 0 -15.5 -2m-.5 -4v4h4","M4 13a8.1 8.1 0 0 0 15.5 2m.5 4v-4h-4"],
  "clock": ["M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0","M12 7v5l3 3"],
  "lock": ["M5 13a2 2 0 0 1 2 -2h10a2 2 0 0 1 2 2v6a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2v-6","M11 16a1 1 0 1 0 2 0a1 1 0 0 0 -2 0","M8 11v-4a4 4 0 1 1 8 0v4"],
  "player-pause": ["M6 6a1 1 0 0 1 1 -1h2a1 1 0 0 1 1 1v12a1 1 0 0 1 -1 1h-2a1 1 0 0 1 -1 -1l0 -12","M14 6a1 1 0 0 1 1 -1h2a1 1 0 0 1 1 1v12a1 1 0 0 1 -1 1h-2a1 1 0 0 1 -1 -1l0 -12"],
  "player-play": ["M7 4v16l13 -8l-13 -8"],
  "chevron-right": ["M9 6l6 6l-6 6"],
  "chevron-left": ["M15 6l-6 6l6 6"],
  "link": ["M9 15l6 -6","M11 6l.463 -.536a5 5 0 0 1 7.071 7.072l-.534 .464","M13 18l-.397 .534a5.068 5.068 0 0 1 -7.127 0a4.972 4.972 0 0 1 0 -7.071l.524 -.463"],
  "x": ["M18 6l-12 12","M6 6l12 12"],
  "loader-2": ["M12 3a9 9 0 1 0 9 9"],
  "trash": ["M4 7l16 0","M10 11l0 6","M14 11l0 6","M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2 -2l1 -12","M9 7v-3a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v3"],
  "search": ["M3 10a7 7 0 1 0 14 0a7 7 0 1 0 -14 0","M21 21l-6 -6"],
  "calendar": ["M4 7a2 2 0 0 1 2 -2h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-12","M16 3v4","M8 3v4","M4 11h16"],
  "send": ["M10 14l11 -11","M21 3l-6.5 18a.55 .55 0 0 1 -1 0l-3.5 -7l-7 -3.5a.55 .55 0 0 1 0 -1l18 -6.5"],
  "alert-circle": ["M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0","M12 8v4","M12 16h.01"],
  "cloud-off": ["M9.58 5.548c.24 -.11 .492 -.207 .752 -.286c1.88 -.572 3.956 -.193 5.444 1c1.488 1.19 2.162 3.007 1.77 4.769h.99c1.913 0 3.464 1.56 3.464 3.486c0 .957 -.383 1.824 -1.003 2.454m-2.997 1.033h-11.343c-2.572 -.004 -4.657 -2.011 -4.657 -4.487c0 -2.475 2.085 -4.482 4.657 -4.482c.13 -.582 .37 -1.128 .7 -1.62","M3 3l18 18"],
  "download": ["M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-2","M7 11l5 5l5 -5","M12 4l0 12"],
  "cloud-download": ["M19 18a3.5 3.5 0 0 0 0 -7h-1a5 4.5 0 0 0 -11 -2a4.6 4.4 0 0 0 -2.1 8.4","M12 13l0 9","M9 19l3 3l3 -3"],
  "message": ["M8 9h8","M8 13h6","M18 4a3 3 0 0 1 3 3v8a3 3 0 0 1 -3 3h-5l-5 3v-3h-2a3 3 0 0 1 -3 -3v-8a3 3 0 0 1 3 -3h12"]
};

/** Tabler icon (v1 1:1: same viewBox/attrs). `className` is appended to "ico". */
export function Icon({ name, className }: { name: string; className?: string }) {
  const paths = ICONS[name] ?? [];
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
         className={"ico" + (className ? " " + className : "")}>
      {paths.map((d, i) => (<path key={i} d={d} />))}
    </svg>
  );
}
