# Harzad — Music Library

# Final UX & Interaction Specification

**Product:** Harzad — Personal Telegram Assistant  
**Feature:** My music + Playlists  
**Version:** 1.0 — Final UX proposal  
**Date:** 2026-11-21  
**Status:** Ready for implementation planning  
**Platform:** Telegram Bot API 10 / python-telegram-bot 22.8  
**Canonical language:** English  
**Future localization:** Persian (RTL)

---

# 1. Product direction

## 1.1 The core idea

The Music Library is a personal collection, not a download history.

Its primary job is to make previously delivered tracks immediately accessible without downloading them again.

The UX should optimize for three behaviors:

1. **Listen again:** Retrieve a track with as few interactions as possible.
    
2. **Find something:** Locate a particular track regardless of library size.
    
3. **Organize:** Group tracks into playlists and send them again conveniently.
    

The Telegram interface should remain a lightweight library manager, not attempt to become a full music player.

## 1.2 Final design decisions

|Area|Decision|
|---|---|
|Entry point|Dedicated My music menu|
|Main navigation|Second-level menu|
|Default destination|Latest|
|Primary discovery|In-library search|
|Secondary discovery|Latest, Often played, Browse all|
|Default sorting|Last requested, newest first|
|List density|5 tracks per screen|
|Track actions|Direct Get again + More|
|Track detail|Yes, for secondary actions|
|Covers|No covers in chat|
|Pagination|Numbered pages with Previous / Next|
|Playlists|First-class menu destination|
|Favorites|Defer explicit favorites; use Often played|
|Mini App|Heavy browsing, visual library and playback|

## 1.3 Design principles

**One primary action per screen.** Get again is the primary action throughout the library.

**Discovery before organization.** Search must be available immediately, rather than requiring users to browse through pages.

**Full information, compact interaction.** Display useful metadata in the message, but keep the keyboard short.

**No destructive actions on the main list.** Removing a track requires opening its secondary actions.

**Stable navigation.** Every screen has an explicit way back.

**Honest feedback.** A successful request, a pending download, a rate limit and an unavailable track must never look like the same state.

---

# 2. Navigation architecture

## 2.1 Navigation diagram

```
HOME
 │
 └── 📚 My music
      │
      ├── 🕒 Latest
      │    └── Track list
      │         ├── ▶ Get again (direct)
      │         └── ⋯ Track details
      │              ├── ▶ Get again
      │              ├── 🎧 Preview
      │              ├── 🎵 Open Spotify
      │              ├── ➕ Add to playlist
      │              └── 🗑 Remove
      │
      ├── 🔎 Search
      │    └── Search prompt
      │         └── Search results
      │              └── Same track list actions
      │
      ├── ⭐ Often played
      │    └── Track list
      │
      ├── 📄 Browse all
      │    └── Sorted track list
      │         ├── Recent
      │         ├── Added
      │         └── A–Z
      │
      └── 🎼 Playlists
           ├── Playlist list
           │    ├── Create playlist
           │    └── Open playlist
           │         ├── ▶ Send all
           │         ├── ➕ Add tracks
           │         ├── ⋯ Track details
           │         ├── ✏ Rename
           │         └── 🗑 Delete
           │
           └── Playlist send progress
                ├── Cancel
                └── Completion
```

The global escape row is retained on every library screen:

```
[📥 Tasks] [🏠 Home]
```

The menu button is not repeated on the main menu itself.

## 2.2 Navigation rationale

The first menu is intentionally retained because the owner explicitly requested a second-level menu.

However, it is not a passive menu. Its destinations are organized around different intentions:

- Latest: retrieve something recently requested.
    
- Search: find a known track.
    
- Often played: retrieve frequently requested tracks.
    
- Browse all: explore the complete collection.
    
- Playlists: manage collections.
    

Search is a first-class destination, not an option hidden inside Browse all.

This distinction matters as the library grows from 64 to 500 or 1,000 tracks.

## 2.3 Tap-count analysis

Tap counts start at Home. A tap includes pressing an inline keyboard button or submitting a text query. Typing the actual query is not counted as a separate tap.

Assumptions:

- Search finds the requested track.
    
- Track list actions are direct callbacks.
    
- Creating a playlist requires submitting its name.
    
- Filling a playlist includes adding one track through library search.
    
- Playlist sending requires explicit confirmation.
    

|Task|64 tracks|500 tracks|
|---|---|---|
|Re-send one of 5 newest|3|3|
|Find and re-send any known track|5|5|
|Remove a known track|6|6|
|Create playlist and add one track|8|8|
|Re-send a playlist|5|5|

### Explanation

**Newest track**

```
Home
 → My music
 → Latest
 → Get again
```

3 taps.

**Any old track**

```
Home
 → My music
 → Search
 → Submit query
 → Get again
```

5 taps.

Search results display the same track list actions as Latest.

**Remove a track**

```
Home
 → My music
 → Search
 → Submit query
 → More
 → Remove
```

6 taps, including the destructive action.

**Create and fill a playlist**

```
Home
 → My music
 → Playlists
 → Create playlist
 → Submit name
 → Add tracks
 → Search
 → Submit query
 → Add selected track
```

9 taps if the final Add action is counted separately. This is a one-track example; adding additional tracks repeats the selection flow.

**Re-send a playlist**

```
Home
 → My music
 → Playlists
 → Open playlist
 → Send all
```

5 taps.

The important acceptance criterion is that any known track remains reachable in five taps regardless of collection size.

---

# 3. Main menu

## 3.1 Screen M01 — My music

**Purpose:** Entry point to all library functionality.

**Reached from:** Home → My music.

**Exact message:**

```
📚 Your music

64 tracks saved

Everything you've received, ready to listen again.

Choose where to start:
```

The track count is dynamic.

**Keyboard:**

```
[🕒 Latest]       [🔎 Search]
[⭐ Often played] [📄 Browse all]
[🎼 Playlists]
[📥 Tasks]        [🏠 Home]
```

|Button|Action|Destination|
|---|---|---|
|Latest|Load latest 5 tracks|M02|
|Search|Start library search|M03|
|Often played|Load frequently requested tracks|M02|
|Browse all|Load complete collection|M02|
|Playlists|Load playlist collection|P01|
|Tasks|Existing global task screen|Global|
|Home|Existing home screen|Global|

All library buttons except global navigation use callback actions.

### Why this menu?

The original v2 menu had only Latest and More. This is insufficient for a collection that grows continuously.

The new menu preserves Latest while introducing Search and Playlists as primary destinations.

There is no need to expose every possible sorting option on this screen.

---

# 4. Track list system

## 4.1 Shared list design

All track lists reuse one common presentation and interaction component.

Its source determines the displayed tracks:

- Latest
    
- Search results
    
- Often played
    
- Browse all
    
- Playlist contents
    

The visual structure stays consistent.

## 4.2 Screen M02 — Track list

**Purpose:** Display multiple tracks with direct playback actions.

**Reached from:** Latest, Often played, Browse all, Search results, or a playlist.

**Example message:**

```
📚 Your music · Latest
64 tracks · Page 1 of 13

1. Encounters at the End of the World
   Thomas Feiner · 3:40
   Requested 3 times · Last 29 min ago

2. Candles
   Resa Saffa Park · 3:32
   Requested once · Last 8 h ago

3. Because You Love Me
   Kaz Hawkins · 5:43
   Requested 2 times · Last 1 d ago

4. Yesterday
   breathe. · 3:24
   Requested 2 times · Last 2 d ago

5. Lost in a Dream
   Guenter Haas · 6:42
   Requested once · Last 3 d ago

Select a track to listen again or view more actions.
```

The metadata is intentionally reduced to:

- Title
    
- Artist
    
- Duration
    
- Request count
    
- Last requested time
    

Album is omitted from the list because it consumes space and is a weak grouping dimension in the current dataset.

Album remains available on the detail screen.

### Keyboard

```
[▶ 1] [⋯ 1]
[▶ 2] [⋯ 2]
[▶ 3] [⋯ 3]
[▶ 4] [⋯ 4]
[▶ 5] [⋯ 5]
[◀ Previous] [Next ▶]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

For the first page, Previous is disabled or omitted.

For the last page, Next is disabled or omitted.

**Button behavior:**

|Button|Action|
|---|---|
|▶ N|Immediately re-send track N|
|⋯ N|Open track detail|
|Previous|Previous page|
|Next|Next page|
|Menu|Return to M01|
|Tasks|Global task screen|
|Home|Global home|

### Important interaction decision

There is exactly one direct playback button and one secondary-action button per track.

This avoids the original v2 layout where three competing buttons appeared for every track.

The primary action remains visually and semantically dominant.

### Track button labels

Use stable short labels:

```
▶ 1
⋯ 1
```

Do not put the complete track title inside the keyboard.

The numbered association between the message and its keyboard is sufficient and avoids Telegram's button-width and mixed-direction problems.

## 4.3 List density

Use five tracks per page.

Reasons:

1. Five tracks provide useful information without overwhelming a phone screen.
    
2. Ten track buttons occupy five keyboard rows.
    
3. Pagination, Menu and global navigation bring the total to eight rows.
    
4. Longer metadata can be displayed without making the message excessively long.
    
5. The number remains consistent across all list types.
    

The screen must never exceed eight keyboard rows.

For a playlist, the same five-track pagination rule applies.

## 4.4 List sorting

|List|Order|
|---|---|
|Latest|Last requested descending|
|Search|Relevance, then last requested descending|
|Often played|Request count descending, then last requested descending|
|Browse all|Last requested descending by default|
|Playlist|Explicit playlist position|

For Browse all, sorting options are available through the sort screen.

## 4.5 Browse all sorting

### Screen M04 — Sort selection

**Message:**

```
📄 Browse all

Choose how your library should be ordered.
```

**Keyboard:**

```
[🕒 Recently requested]
[📅 Recently added]
[🔤 Title A–Z]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

The selected sort can be reflected in the list heading:

```
📚 Your music · Title A–Z
```

Do not add another sort row to every track list. Sorting belongs to the Browse all entry flow.

---

# 5. Track detail

## 5.1 Screen M05 — Track details

**Purpose:** Provide secondary actions without overcrowding the main list.

**Reached from:** ⋯ N on any track list.

**Example message:**

```
🎵 Track details

Encounters at the End of the World

Artist: Thomas Feiner
Album: Encounters at the End of the World
Duration: 3:40

Requested: 3 times
Last requested: 29 minutes ago
Added: 12 days ago

What would you like to do?
```

### Keyboard

```
[▶ Get again]
[🎧 30s Preview]
[🎵 Open on Spotify]
[➕ Add to playlist]
[🗑 Remove from library]
[◀ Back to tracks]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

### Button behavior

|Button|Action|
|---|---|
|Get again|Start normal re-send flow|
|30s Preview|Start preview lookup|
|Open on Spotify|URL button using spotify_url|
|Add to playlist|Open playlist selector|
|Remove from library|Open confirmation screen|
|Back to tracks|Return to originating list|
|Menu|M01|
|Tasks / Home|Global|

The originating list context must be preserved when returning from details.

For example, opening a track from Search results and returning should return to those Search results, not Latest.

## 5.2 Is a detail screen worth the tap?

Yes.

The main list is optimized for retrieval. The detail screen is optimized for management.

Separating these purposes prevents the list from becoming a crowded collection of controls.

The detail screen is also the correct place to expose future actions without redesigning the main list.

## 5.3 Cover policy

**Decision: No cover images in Telegram chat.**

This follows the owner's explicit preference.

The benefits do not justify the additional vertical space and image-message complexity.

Covers belong in the Mini App, where a visual grid and player interface can use them effectively.

Do not revisit this decision for the chat implementation.

---

# 6. Search

Search is the most important improvement over v2.

## 6.1 Screen M03 — Search prompt

**Reached from:** M01 → Search.

**Message:**

```
🔎 Search your music

Send a track title or artist name.

You can search using part of a title or artist.

Send /cancel to stop searching.
```

**Keyboard:**

```
[✖ Cancel]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

On pressing Search, the bot enters a dedicated conversation state.

The next text message is treated as a library search query.

## 6.2 Search behavior

Recommended matching:

- Case-insensitive.
    
- Partial title match.
    
- Partial artist match.
    
- Combined title/artist matching.
    
- Unicode-aware matching.
    
- Normalize whitespace.
    
- Preserve original display values.
    

Search should operate on stored library records only.

Do not invoke Spotify search or the existing download workflow from this screen.

Those flows remain separate.

### Minimum query length

Use a minimum of two Unicode characters.

Reason: one-character searches can generate excessive results, especially in multilingual collections.

However, allow an exact one-character query if the implementation can safely return a small result set. Otherwise, return a helpful prompt.

## 6.3 Screen M06 — Search results

**Example:**

```
🔎 Search results: because

Found 2 tracks.

1. Because You Love Me
   Kaz Hawkins · 5:43
   Requested 2 times · Last 1 d ago

2. Because
   Artist Name · 3:12
   Requested once · Last 8 d ago

Select a track to listen again or view more actions.
```

**Keyboard:**

```
[▶ 1] [⋯ 1]
[▶ 2] [⋯ 2]
[🔎 New search]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

For more than five results, use the standard five-track pagination.

The heading must preserve a short representation of the original query.

## 6.4 Zero results

**Message:**

```
🔎 No matching tracks

No tracks matched "queen".

Try another title or artist name.
```

**Keyboard:**

```
[🔎 Search again]
[📚 Browse all]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

## 6.5 Search input cancellation

`/cancel` cancels the search state.

Toast:

```
Search cancelled.
```

Return to M01.

The Cancel button should do the same.

## 6.6 Search snapshots and expiration

Search results use the same frozen-list snapshot mechanism as normal lists.

Each rendered search result screen receives a new snapshot token.

All track buttons refer to the frozen result positions.

Expiration behavior:

```
This list has expired.

Open My music and search again.
```

The user is returned to the global My music entry point when reopening.

Do not silently reinterpret an old button against a new search result set.

---

# 7. Often played

## 7.1 Screen M07 — Often played

**Purpose:** Quickly access frequently requested tracks.

This is a derived view, not a manually maintained favorites system.

### Selection rule

Use:

```
request_count >= 2
```

Sort by:

1. Request count descending.
    
2. Last requested descending.
    

If there are no qualifying tracks:

```
⭐ Often played

Your frequently requested tracks will appear here as you listen again.
```

Keyboard:

```
[🕒 Latest]
[🔎 Search]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

If tracks exist, display them using the standard M02 list format.

### Why not add Favorites now?

The current data does not contain an explicit favorite field.

Request count already captures a meaningful behavior.

Adding a manual star action now would introduce another concept, state and management workflow without enough evidence that it is needed.

Explicit favorites can be considered after real usage.

---

# 8. Playlists

Playlists are a first-class section inside My music.

They should ship using the same navigation architecture as the rest of the library.

## 8.1 Playlist rules

- Maximum 50 playlists.
    
- Maximum 200 tracks per playlist.
    
- A track can belong to multiple playlists.
    
- Playlist ordering is explicit.
    
- Adding a track does not remove it from other playlists.
    
- Removing a track from a playlist does not remove it from the library.
    
- Deleting a playlist does not delete its tracks.
    
- Sending a playlist sends its tracks in playlist order.
    
- Bulk delivery is paced approximately one message per second.
    

The library remains the source of truth for track metadata.

A playlist stores membership and position, not duplicate track records.

## 8.2 Screen P01 — Playlist list

**Reached from:** M01 → Playlists.

**Message:**

```
🎼 Your playlists

3 playlists

Organize your music into collections.
```

Example entries:

```
1. Road Trip
   18 tracks

2. Persian Favorites
   12 tracks

3. Late Night
   7 tracks
```

**Keyboard:**

```
[📂 1 · Road Trip]
[📂 2 · Persian Favorites]
[📂 3 · Late Night]
[➕ Create playlist]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

Playlist button labels must be truncated to remain within approximately 30 characters.

The full names appear in the message.

## 8.3 Empty playlists

**Message:**

```
🎼 Your playlists

You haven't created any playlists yet.

Create one to organize your music.
```

**Keyboard:**

```
[➕ Create playlist]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

## 8.4 Screen P02 — Create playlist prompt

**Message:**

```
➕ Create playlist

Send a name for your new playlist.

You can rename it later.

Maximum: 50 playlists.
```

**Keyboard:**

```
[✖ Cancel]
[📚 Playlists]
[📥 Tasks] [🏠 Home]
```

The next user message is the playlist name.

Validation:

- Trim surrounding whitespace.
    
- Reject empty names.
    
- Recommended maximum: 60 Unicode characters.
    
- Preserve Persian and mixed-direction names.
    
- Do not silently truncate.
    
- Check duplicate names case-insensitively where appropriate.
    

On success:

```
Playlist created.
```

Open the new playlist detail screen.

At 50 playlists:

```
You can create up to 50 playlists.
```

## 8.5 Screen P03 — Playlist detail

**Example:**

```
🎼 Road Trip

18 tracks

1. Encounters at the End of the World
   Thomas Feiner · 3:40

2. Because You Love Me
   Kaz Hawkins · 5:43

3. Yesterday
   breathe. · 3:24

4. Lost in a Dream
   Guenter Haas · 6:42

5. Aşkım Ve Gururum
   Kamuran Akkor · 4:01

Page 1 of 4
```

**Keyboard:**

```
[▶ Send all]
[➕ Add tracks]
[⋯ 1] [⋯ 2]
[⋯ 3] [⋯ 4]
[⋯ 5]
[◀ Previous] [Next ▶]
[✏ Rename] [🗑 Delete]
[◀ Playlists]
[📥 Tasks] [🏠 Home]
```

For the five-track list, there are five track-action rows, plus action, pagination, management, back and escape rows. That exceeds the eight-row target.

Therefore, the final implementation should use a compact two-stage playlist action layout:

**Playlist detail keyboard:**

```
[▶ Send all]
[➕ Add tracks]
[⋯ Track actions]
[✏ Rename] [🗑 Delete]
[◀ Playlists]
[📥 Tasks] [🏠 Home]
```

Track actions open a separate paginated playlist contents screen.

### Screen P04 — Playlist contents

```
🎼 Road Trip · Tracks

18 tracks · Page 1 of 4

1. Encounters at the End of the World
   Thomas Feiner · 3:40

2. Because You Love Me
   Kaz Hawkins · 5:43

3. Yesterday
   breathe. · 3:24

4. Lost in a Dream
   Guenter Haas · 6:42

5. Aşkım Ve Gururum
   Kamuran Akkor · 4:01
```

Keyboard:

```
[⋯ 1] [⋯ 2]
[⋯ 3] [⋯ 4]
[⋯ 5]
[◀ Previous] [Next ▶]
[◀ Playlist]
[📥 Tasks] [🏠 Home]
```

Selecting a track opens M05 with playlist context.

The detail screen should expose an additional action:

```
[➖ Remove from playlist]
```

This removes membership only.

It must never delete the underlying library track.

## 8.6 Add tracks flow

From P03:

```
[➕ Add tracks]
```

Open P05:

```
➕ Add tracks

Search your music to add tracks to Road Trip.

You can add multiple tracks.
```

Keyboard:

```
[🔎 Search library]
[◀ Playlist]
[📥 Tasks] [🏠 Home]
```

Search uses the existing library search mechanism.

The result list has an Add action instead of direct Get again:

```
[➕ 1]
[➕ 2]
[➕ 3]
[➕ 4]
[➕ 5]
[🔎 New search]
[◀ Playlist]
[📥 Tasks] [🏠 Home]
```

After adding a track:

```
Added to Road Trip.
```

The result list remains open so multiple tracks can be added without restarting the flow.

For an already-present track:

```
This track is already in the playlist.
```

At 200 tracks:

```
This playlist can hold up to 200 tracks.
```

The playlist count updates after every successful addition.

## 8.7 Rename playlist

From P03:

```
[✏ Rename]
```

Message:

```
✏ Rename playlist

Send the new name for Road Trip.
```

The next text message is validated.

On success:

```
Playlist renamed.
```

Return to P03.

Cancellation:

```
Rename cancelled.
```

## 8.8 Delete playlist

From P03:

```
[🗑 Delete]
```

### Screen P06 — Delete confirmation

```
🗑 Delete playlist?

Road Trip

This will delete the playlist and its organization.

Your saved music will remain in My music.
```

Keyboard:

```
[🗑 Yes, delete]
[◀ Keep playlist]
[📥 Tasks] [🏠 Home]
```

On success:

```
Playlist deleted.
```

Return to P01.

## 8.9 Send all

This is the most important playlist operation.

It must be explicit and must not start sending immediately when a playlist is opened.

### Screen P07 — Send confirmation

```
▶ Send playlist?

Road Trip

18 tracks will be sent to this chat.

Messages will be sent one at a time.

You can cancel while sending.
```

Keyboard:

```
[▶ Start sending]
[◀ Back to playlist]
[📥 Tasks] [🏠 Home]
```

On Start sending:

- Create a batch task.
    
- Freeze the playlist's ordered track membership for that task.
    
- Start sending at approximately one message per second.
    
- Use the existing re-delivery ladder for each track.
    
- Update progress.
    
- Allow cancellation.
    
- Do not duplicate already-sent tracks if the worker resumes.
    

### Screen P08 — Sending progress

```
📤 Sending Road Trip

Sent: 7 / 18
Remaining: 11

Current track:
Yesterday

Sending your playlist...
```

Keyboard:

```
[✖ Cancel sending]
[📥 Tasks] [🏠 Home]
```

### Completion

```
✅ Playlist sent

Road Trip

18 of 18 tracks sent successfully.
```

Keyboard:

```
[🎼 Open playlist]
[📚 My music]
[🏠 Home]
```

### Partial failure

```
⚠ Playlist partially sent

Road Trip

14 of 18 tracks were sent.

4 tracks could not be delivered.

You can review the failed tracks and try again.
```

The failed-track review must not automatically resend successful tracks.

### Cancellation

```
Playlist sending cancelled.

7 of 18 tracks were sent.
```

The count must reflect actual successful deliveries.

### Rate limit

If Telegram returns 429:

- Read `retry_after`.
    
- Pause the batch.
    
- Show the waiting state.
    
- Resume only after the allowed interval.
    
- Preserve the sent/remaining counts.
    

Example:

```
⏳ Telegram is limiting messages.

Sending will resume shortly.

Please wait 12 seconds.
```

The Cancel action remains available.

---

# 9. Pagination and positioning

## 9.1 Replace Newer / Older

Use numbered page information and Previous / Next.

Example:

```
Page 4 of 13
```

Keyboard:

```
[◀ Previous] [Next ▶]
```

This provides orientation without introducing long page-number keyboards.

## 9.2 Page boundaries

First page:

- No Previous button.
    
- Next available.
    

Middle pages:

- Previous and Next available.
    

Last page:

- Previous available.
    
- No Next button.
    

## 9.3 Search-first principle

Pagination is a browsing mechanism, not the primary finding mechanism.

At 500 tracks, browsing 100 pages of five tracks is not acceptable as the default way to find something.

Search solves that problem.

## 9.4 Snapshot integrity

Every list-render operation creates a new immutable snapshot.

The callback must identify:

- Snapshot token.
    
- Action.
    
- Item position.
    
- Relevant context.
    

Keep payloads within Telegram's 64-byte limit.

Avoid embedding titles, Spotify IDs or other large data in callbacks.

---

# 10. State matrix

All states below are part of the required implementation.

## 10.1 Library states

|State|Exact response|Actions|
|---|---|---|
|Empty library|No music saved yet. Your tracks will appear here automatically when you receive music.|Search music, Home|
|One track|Show the single track, Page 1 of 1|Get again, More|
|Five tracks|Show five tracks, Page 1 of 1|Standard actions|
|64 tracks|Show five tracks, Page 1 of 13|Pagination, Search|
|500 tracks|Show five tracks, Page 1 of 100|Pagination, Search|
|No often-played tracks|Frequently requested tracks will appear here as you listen again.|Latest, Search, Menu|
|Database unavailable|Your music library is temporarily unavailable. Please try again shortly.|Try again, Home|
|Expired snapshot|This list has expired. Open My music and try again.|Reopen My music|
|Unknown callback|This action is no longer available. Please reopen My music.|Reopen My music|

## 10.2 Re-send states

The existing delivery ladder remains unchanged.

1. Instant cloud relay.
    
2. Copy original Telegram message where applicable.
    
3. Re-download through the normal task flow.
    
4. Honest failure if delivery is impossible.
    

The UX must not expose internal delivery implementation details unless they affect waiting time or outcome.

### Instant success

The audio message is sent.

No unnecessary intermediate screen is required.

### Re-download started

Use the existing task status system.

Example:

```
📥 Preparing your track

This track needs to be downloaded again.

You can cancel the task while it is processing.
```

Keyboard:

```
[✖ Cancel task]
[📥 Tasks] [🏠 Home]
```

### Unavailable track

```
⚠ Track unavailable

We couldn't retrieve this track.

It may no longer be available from its source.

You can search for it again.
```

Keyboard:

```
[🔎 Search music]
[📚 My music]
[🏠 Home]
```

Do not claim that the track was deleted from the library unless it actually was.

### Re-send success

The newly delivered track message is sent.

The corresponding library row is refreshed, including its cloud handle and last-requested timestamp.

## 10.3 Preview states

### Started

No extra card is necessary for the normal 1–3 second lookup.

### Success

Send the preview as an audio message.

### Unavailable

Toast:

```
Preview is currently unavailable.
```

The user remains on M05.

### Invalid or missing source

```
Preview could not be prepared.
```

No library data should be removed.

## 10.4 Remove states

### Confirmation

Recommended addition to v2:

Do not remove immediately from a potentially accidental secondary-action tap.

Screen:

```
🗑 Remove track?

Encounters at the End of the World
Thomas Feiner

This removes the track from My music.
It does not delete previously sent messages.
```

Keyboard:

```
[🗑 Yes, remove]
[◀ Keep track]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

On confirmation:

- Delete the library row.
    
- Remove the track from playlist memberships according to the data model's referential behavior.
    
- Refresh the originating list.
    
- Return to the originating list context.
    

If the product wants playlists to retain unavailable/deleted entries, that requires a separate tombstone policy. The recommended initial behavior is to remove the deleted track from playlists too and keep the operation consistent.

Success toast:

```
Track removed from My music.
```

## 10.5 Database unavailable

Exact message:

```
⚠ My music is temporarily unavailable.

Your library data could not be loaded.

Please try again shortly.
```

Keyboard:

```
[🔁 Try again]
[🏠 Home]
```

Do not display an empty-library screen when the database is unavailable.

These are fundamentally different states.

## 10.6 429 rate limit

For a single-track send:

```
⏳ Telegram is limiting messages.

Please wait 12 seconds, then try again.
```

Keyboard:

```
[🔁 Try again]
[📥 Tasks] [🏠 Home]
```

The displayed wait is dynamic.

The retry button must not bypass Telegram's retry window.

For batch sends, retain the batch task and resume automatically when permitted.

## 10.7 Group-chat tap

Every library callback must verify private-chat context.

In a group:

- Do not show library content.
    
- Do not execute a library operation.
    
- Do not expose track names or playlist names.
    
- Answer with one short callback toast.
    

Exact text:

```
My music is available in private chat only.
```

No group message should be sent.

## 10.8 Stale card / process restart

The 300-second snapshot TTL remains.

When an old callback is received:

```
This list has expired. Open My music again.
```

Use a callback toast where supported.

The user must reopen the library and regenerate the screen.

Never guess the original item position against new data.

---

# 11. Copy deck

All user-facing strings must be stored in a flat localization dictionary.

The following keys are the canonical English copy.

## 11.1 Main library

|Key|English value|
|---|---|
|music.menu.title|📚 Your music|
|music.menu.count|{count} tracks saved|
|music.menu.description|Everything you've received, ready to listen again.|
|music.menu.prompt|Choose where to start:|
|music.latest|🕒 Latest|
|music.search|🔎 Search|
|music.often_played|⭐ Often played|
|music.browse_all|📄 Browse all|
|music.playlists|🎼 Playlists|
|music.tasks|📥 Tasks|
|music.home|🏠 Home|
|music.menu|📚 Menu|
|music.list.title|📚 Your music · {source}|
|music.list.count|{count} tracks · Page {page} of {pages}|
|music.list.prompt|Select a track to listen again or view more actions.|
|music.list.request_count|Requested {count} times|
|music.list.requested_once|Requested once|
|music.list.last_requested|Last {time} ago|
|music.list.get_again|▶ Get again|
|music.list.more|⋯ More|
|music.list.previous|◀ Previous|
|music.list.next|Next ▶|
|music.list.page|Page {page} of {pages}|

## 11.2 Search

|Key|English value|
|---|---|
|music.search.title|🔎 Search your music|
|music.search.prompt|Send a track title or artist name.|
|music.search.hint|You can search using part of a title or artist.|
|music.search.cancel_hint|Send /cancel to stop searching.|
|music.search.results|🔎 Search results: {query}|
|music.search.found|Found {count} tracks.|
|music.search.no_results|🔎 No matching tracks|
|music.search.no_results_description|No tracks matched "{query}".|
|music.search.try_again|Try another title or artist name.|
|music.search.new|🔎 New search|
|music.search.again|🔎 Search again|
|music.search.cancelled|Search cancelled.|
|music.search.too_short|Please enter at least 2 characters.|

## 11.3 Track detail

|Key|English value|
|---|---|
|music.detail.title|🎵 Track details|
|music.detail.artist|Artist: {artist}|
|music.detail.album|Album: {album}|
|music.detail.duration|Duration: {duration}|
|music.detail.request_count|Requested: {count} times|
|music.detail.added|Added: {time} ago|
|music.detail.last_requested|Last requested: {time} ago|
|music.detail.prompt|What would you like to do?|
|music.detail.get_again|▶ Get again|
|music.detail.preview|🎧 30s Preview|
|music.detail.spotify|🎵 Open on Spotify|
|music.detail.add_playlist|➕ Add to playlist|
|music.detail.remove|🗑 Remove from library|
|music.detail.back|◀ Back to tracks|

## 11.4 Often played and sorting

|Key|English value|
|---|---|
|music.often.title|⭐ Often played|
|music.often.empty|Your frequently requested tracks will appear here as you listen again.|
|music.browse.title|📄 Browse all|
|music.sort.prompt|Choose how your library should be ordered.|
|music.sort.recent|🕒 Recently requested|
|music.sort.added|📅 Recently added|
|music.sort.az|🔤 Title A–Z|

## 11.5 Playlists

|Key|English value|
|---|---|
|playlist.title|🎼 Your playlists|
|playlist.count|{count} playlists|
|playlist.description|Organize your music into collections.|
|playlist.empty|You haven't created any playlists yet.|
|playlist.empty_hint|Create one to organize your music.|
|playlist.create|➕ Create playlist|
|playlist.create_prompt|Send a name for your new playlist.|
|playlist.create_hint|You can rename it later.|
|playlist.create_limit|Maximum: 50 playlists.|
|playlist.created|Playlist created.|
|playlist.create_limit_error|You can create up to 50 playlists.|
|playlist.detail.title|🎼 {name}|
|playlist.detail.count|{count} tracks|
|playlist.add|➕ Add tracks|
|playlist.send_all|▶ Send all|
|playlist.track_actions|⋯ Track actions|
|playlist.rename|✏ Rename|
|playlist.delete|🗑 Delete|
|playlist.back|◀ Playlists|
|playlist.add_prompt|Search your music to add tracks to {name}.|
|playlist.add_multiple|You can add multiple tracks.|
|playlist.search|🔎 Search library|
|playlist.added|Added to {name}.|
|playlist.already_exists|This track is already in the playlist.|
|playlist.track_limit|This playlist can hold up to 200 tracks.|
|playlist.rename_prompt|Send the new name for {name}.|
|playlist.renamed|Playlist renamed.|
|playlist.rename_cancelled|Rename cancelled.|
|playlist.delete_confirm|🗑 Delete playlist?|
|playlist.delete_description|This will delete the playlist and its organization.|
|playlist.delete_preserve_tracks|Your saved music will remain in My music.|
|playlist.delete_confirm_button|🗑 Yes, delete|
|playlist.keep|◀ Keep playlist|
|playlist.deleted|Playlist deleted.|

## 11.6 Playlist sending

|Key|English value|
|---|---|
|playlist.send_confirm|▶ Send playlist?|
|playlist.send_count|{count} tracks will be sent to this chat.|
|playlist.send_pacing|Messages will be sent one at a time.|
|playlist.send_cancel_hint|You can cancel while sending.|
|playlist.send_start|▶ Start sending|
|playlist.sending|📤 Sending {name}|
|playlist.sent_count|Sent: {sent} / {total}|
|playlist.remaining|Remaining: {remaining}|
|playlist.current|Current track: {title}|
|playlist.sending_description|Sending your playlist...|
|playlist.cancel|✖ Cancel sending|
|playlist.complete|✅ Playlist sent|
|playlist.complete_count|{sent} of {total} tracks sent successfully.|
|playlist.partial|⚠ Playlist partially sent|
|playlist.partial_count|{sent} of {total} tracks were sent.|
|playlist.failed_count|{count} tracks could not be delivered.|
|playlist.failed_hint|You can review the failed tracks and try again.|
|playlist.cancelled|Playlist sending cancelled.|
|playlist.cancelled_count|{sent} of {total} tracks were sent.|
|playlist.open|🎼 Open playlist|

## 11.7 Errors and feedback

|Key|English value|
|---|---|
|music.error.db|Your music library is temporarily unavailable. Please try again shortly.|
|music.error.expired|This list has expired. Open My music and try again.|
|music.error.unknown|This action is no longer available. Please reopen My music.|
|music.error.unavailable|⚠ Track unavailable|
|music.error.unavailable_description|We couldn't retrieve this track.|
|music.error.source|It may no longer be available from its source.|
|music.error.search_again|You can search for it again.|
|music.error.preview|Preview is currently unavailable.|
|music.error.preview_prepare|Preview could not be prepared.|
|music.error.group|My music is available in private chat only.|
|music.error.rate_limit|Telegram is limiting messages.|
|music.error.rate_wait|Please wait {seconds} seconds, then try again.|
|music.error.invalid_input|Please send a valid name.|
|music.error.cancelled|Action cancelled.|
|music.success.removed|Track removed from My music.|
|music.success.search_cancelled|Search cancelled.|
|music.error.not_found|This track is no longer in your library.|

**Toast length:** Keep all callback toast messages under Telegram's 64-character limit. The longer explanatory messages above belong in edited cards, not callback toasts.

---

# 12. Edge-case specifications

## 12.1 Very long title (109 characters)

Example:

`Flower In A Hurricane (feat. Tame Impala, SZA, King Gizzard & the Lizard Wizard) [From GTAVI: Original Score]`

Rules:

- Never truncate the stored title.
    
- Never put the title inside a keyboard button.
    
- Display it on its own message line.
    
- Preserve the original title.
    
- Allow Telegram to wrap it naturally.
    
- Do not split the title manually into artificial fragments.
    

Example:

```
8. Flower In A Hurricane (feat. Tame Impala, SZA,
   King Gizzard & the Lizard Wizard) [From GTAVI:
   Original Score]
   Justice · 4:12
```

The exact line wrapping is Telegram-controlled.

## 12.2 Mixed Persian/Latin title

Example:

`OUD SILENCE " صَمْت "`

Rules:

- Preserve original Unicode.
    
- Do not manually reverse the text.
    
- Avoid embedding dynamic titles inside callback labels.
    
- Use separate lines for title, artist and metadata.
    
- Apply appropriate bidirectional isolation in message formatting if supported by the rendering layer.
    
- Test both Telegram light and dark themes.
    
- Test RTL names with Latin artist names and punctuation.
    

## 12.3 One track

Do not force pagination.

Message:

```
📚 Your music · Latest
1 track

1. Track title
   Artist · 3:40
   Requested once · Last 2 h ago
```

Keyboard:

```
[▶ 1] [⋯ 1]
[📚 Menu]
[📥 Tasks] [🏠 Home]
```

## 12.4 500 tracks

- Five tracks per page.
    
- 100 pages.
    
- Search remains the primary retrieval mechanism.
    
- Never show 500 inline buttons.
    
- Avoid loading the entire collection into a single message.
    
- Database queries should be paginated.
    
- Each result screen gets its own snapshot.
    

## 12.5 Deleted or inaccessible source

A source may no longer provide a downloadable file.

Do not automatically remove the track from the library.

Show the unavailable state and allow the user to use the existing search/download flow separately.

## 12.6 Stale callback after restart

All old snapshots become invalid after process restart.

Show the expired state and require reopening.

No silent fallback to another track.

## 12.7 Telegram 429

- Respect `retry_after`.
    
- Do not repeatedly retry before the allowed time.
    
- Preserve task state.
    
- Make cancellation possible.
    
- Do not create duplicate sends on retry.
    

---

# 13. Persian and RTL localization

Persian is the planned next localization.

The underlying navigation architecture should not change.

## 13.1 Labels

Keep button labels short and emoji-anchored.

Examples:

|English|Persian direction|
|---|---|
|Latest|جدیدترین|
|Search|جستجو|
|Often played|پخش‌شده‌های پرتکرار|
|Browse all|مشاهده همه|
|Playlists|پلی‌لیست‌ها|
|Get again|پخش دوباره|
|More|بیشتر|
|Remove|حذف|

Final Persian translations should be reviewed for natural conversational language rather than literal translation.

## 13.2 Layout

Telegram controls the actual keyboard layout and text rendering.

Do not assume that the order of buttons in source code will automatically produce the desired visual RTL order.

Test keyboard ordering in Persian Telegram clients.

Where necessary, reverse the logical button sequence to achieve the intended visual order.

## 13.3 Mixed-direction content

Track titles and artist names are external metadata and must not be translated.

Use directional isolation around dynamic Latin content where supported.

Do not modify the stored title to make it visually convenient.

## 13.4 Copy expansion

Allow approximately 30% more space for translated text.

The five-track density should remain unchanged unless real Telegram rendering tests prove it unusable.

---

# 14. Telegram chat vs Mini App

## 14.1 What stays in chat

Telegram remains the primary interface for quick operations.

|Capability|Chat|
|---|---|
|Open My music|Yes|
|Latest tracks|Yes|
|Search library|Yes|
|Often played|Yes|
|Browse and sort|Yes|
|Re-send track|Yes|
|30-second preview|Yes|
|Remove track|Yes|
|Open Spotify|Yes|
|Create playlist|Yes|
|Rename playlist|Yes|
|Delete playlist|Yes|
|Add/remove playlist tracks|Yes|
|Send all playlist tracks|Yes|
|Task status and cancellation|Yes|

## 14.2 What belongs in the Mini App

The Mini App is the visual browsing and playback environment.

|Capability|Mini App|
|---|---|
|Cover-based grid|Yes|
|Rich track cards|Yes|
|Full library search|Yes|
|Continuous music player|Yes|
|Playback queue|Yes|
|Playlist drag-and-drop|Yes|
|Bulk playlist editing|Yes|
|Visual playlist builder|Yes|
|Rich sorting and filtering|Yes|
|Album/artist visual browsing|Yes|

## 14.3 When does chat stop being adequate?

There is no hard technical threshold at 500 tracks.

A five-track page and database-backed search can remain usable beyond that.

However, the UX value of the Mini App increases with collection size.

Recommended planning:

|Library size|Primary experience|
|---|---|
|0–100|Telegram chat|
|100–500|Telegram chat + prepare Mini App|
|500–1,000|Both experiences|
|1,000+|Mini App becomes preferred browsing interface|

These are product-planning thresholds, not Telegram technical limits.

The chat interface should remain useful even after the Mini App ships.

---

# 15. Implementation contract

## 15.1 Snapshot model

Every list screen must create an immutable snapshot.

Conceptual structure:

```
MusicSnapshot:
    token: str
    owner_id: int
    chat_id: int
    screen_type: str
    created_at: datetime
    expires_at: datetime
    track_ids: list[str]
    page: int
    page_size: int
    context: dict
```

The actual implementation should use the project's established model and naming conventions.

A snapshot is scoped to the private user and chat.

Do not accept callbacks from another user.

## 15.2 Callback design

Suggested callback families:

```
music:menu
music:latest
music:search
music:often
music:browse
music:sort
music:page

music:get:<token>:<index>
music:detail:<token>:<index>
music:preview:<token>:<index>
music:remove:<token>:<index>
music:spotify:<token>:<index>

playlist:list
playlist:create
playlist:open:<id>
playlist:add:<id>
playlist:remove:<id>:<track>
playlist:rename:<id>
playlist:delete:<id>
playlist:send:<id>
playlist:cancel:<task>
```

These are conceptual callback families, not a mandate to encode every identifier exactly this way.

Before implementation, calculate actual serialized byte lengths.

Telegram callback data must never exceed 64 bytes.

Prefer compact tokens and server-side state over embedding full identifiers.

## 15.3 Callback security

Every callback must verify:

1. Private chat.
    
2. Correct owner.
    
3. Valid snapshot or entity identifier.
    
4. Snapshot expiration.
    
5. Action permission.
    
6. Track or playlist still exists.
    
7. Task state permits the requested operation.
    

Do not trust a callback merely because it originated from a previously generated keyboard.

## 15.4 Database behavior

Recommended operations:

- Search using indexed title/artist data.
    
- Paginate using deterministic ordering.
    
- Fetch only the required page.
    
- Use transactions for playlist membership changes.
    
- Enforce playlist caps at the database/service layer.
    
- Keep playlist order stable.
    
- Avoid duplicate memberships.
    
- Refresh track metadata and delivery handles after successful re-send.
    

For deletions, make playlist membership cleanup explicit.

## 15.5 Editing and sending messages

Use in-place message editing for navigation.

New audio deliveries remain separate Telegram messages.

After successful re-send:

- Update the library record.
    
- Do not unnecessarily replace the current navigation card.
    
- Keep the user's browsing context intact.
    

For long-running tasks, use the existing Tasks subsystem rather than inventing a second task manager.

---

# 16. Build order

The implementation should be incremental.

Do not attempt to introduce all playlist behavior in one large change.

## Phase A — Fix the library's core UX

### A1. Navigation menu

Replace the v2 menu with:

- Latest
    
- Search
    
- Often played
    
- Browse all
    
- Playlists placeholder or destination
    

Implement the shared menu and callback handling.

### A2. Shared track list

Refactor all track lists to use:

- Five tracks per page.
    
- Numbered direct Get again actions.
    
- Numbered More actions.
    
- Previous / Next.
    
- Menu.
    
- Global escape row.
    

Preserve source context.

### A3. Track details

Implement the secondary-action screen.

Move Preview, Spotify, Add to playlist and Remove here.

Add remove confirmation.

### A4. Library search

Implement:

- Search prompt.
    
- Conversation state.
    
- Input validation.
    
- Partial title and artist matching.
    
- Search result list.
    
- Zero-result state.
    
- Cancellation.
    
- Snapshot handling.
    

This is the highest-value functional improvement.

### A5. Often played and sorting

Implement derived Often played.

Implement Browse all sort selection.

### A6. State reconciliation

Verify:

- Database errors.
    
- Expired callbacks.
    
- Restarted process.
    
- 429 responses.
    
- Preview errors.
    
- Re-send failures.
    
- Group-chat protection.
    

At this point, the reworked Music Library is complete without Playlists.

## Phase B — Playlists

### B1. Data model

Implement playlist records and membership.

Enforce:

- 50 playlists per user.
    
- 200 tracks per playlist.
    
- Unique membership.
    
- Explicit ordering.
    

### B2. Playlist navigation

Implement:

- Playlist list.
    
- Empty state.
    
- Playlist detail.
    
- Playlist contents.
    
- Create.
    
- Rename.
    
- Delete confirmation.
    

### B3. Track membership

Implement:

- Add from library search.
    
- Duplicate membership handling.
    
- Remove from playlist.
    
- Capacity errors.
    

### B4. Send all

Implement:

- Confirmation.
    
- Batch task.
    
- Ordered delivery.
    
- Progress.
    
- Cancellation.
    
- Rate-limit pause/resume.
    
- Partial failure handling.
    
- Completion.
    

### B5. Regression

Ensure playlist actions do not alter the existing library's behavior.

## Phase C — Localization readiness

- Move every string to the flat copy deck.
    
- Audit dynamic formatting.
    
- Audit emoji labels.
    
- Add Persian translations.
    
- Verify RTL rendering.
    
- Test mixed Latin/Persian metadata.
    

Persian localization can ship separately from the English UX rework.

## Phase D — Mini App

Keep this out of the current chat implementation.

Begin once the chat library and playlist workflows are stable.

---

# 17. Acceptance criteria

The implementation is considered complete when all of the following pass.

## Navigation

- My music opens the second-level menu.
    
- Latest is reachable in one additional tap.
    
- Search is directly accessible from the menu.
    
- Playlists are directly accessible from the menu.
    
- Every screen has a back/navigation path.
    
- Global Home and Tasks remain available.
    

## Track lists

- Exactly five tracks per page by default.
    
- One direct Get again action per track.
    
- One secondary More action per track.
    
- No cover images.
    
- Metadata is complete enough to identify tracks.
    
- Pagination shows page position.
    
- All buttons refer to immutable snapshots.
    
- Keyboard never exceeds eight rows.
    

## Search

- Search accepts partial titles.
    
- Search accepts partial artists.
    
- Search handles Unicode.
    
- Search results use the shared list.
    
- Zero results have a dedicated state.
    
- Search can be cancelled.
    
- Expired search results cannot act on incorrect tracks.
    

## Track management

- Get again works through the existing delivery ladder.
    
- Preview has an unavailable state.
    
- Spotify opens the stored URL.
    
- Remove requires confirmation.
    
- Removal does not delete previously delivered messages.
    
- Detail back navigation restores the original list context.
    

## Playlists

- Create playlist.
    
- Rename playlist.
    
- Delete playlist.
    
- Add tracks.
    
- Remove tracks from playlists.
    
- Prevent duplicate membership.
    
- Enforce 50-playlist limit.
    
- Enforce 200-track limit.
    
- Preserve playlist order.
    
- Send all.
    
- Progress display.
    
- Cancellation.
    
- Rate-limit recovery.
    
- Partial failure handling.
    
- Deleting a playlist never deletes library tracks.
    

## Reliability

- Database failure does not look like an empty library.
    
- Expired callback produces a clear response.
    
- Process restart invalidates old snapshots safely.
    
- 429 is handled using retry_after.
    
- Group-chat interactions reveal no library information.
    
- Every callback validates its owner and context.
    
- All callback payloads are at most 64 bytes.
    

## Performance and usability

- Five newest tracks reachable in three taps from Home.
    
- Any known track reachable in five taps from Home.
    
- Search remains practical at 500 tracks.
    
- No keyboard label exceeds approximately 30 characters.
    
- No screen exceeds eight keyboard rows.
    
- Long titles and mixed RTL metadata render acceptably.
    

---

# 18. Final recommendation

The most important change is not adding more buttons or making the track cards visually richer.

It is changing the library from a paginated history browser into a searchable personal collection.

The final experience has three clear layers:

**Layer 1 — Navigation**

```
📚 My music

Latest · Search
Often played · Browse all
Playlists
```

**Layer 2 — Collection**

```
Five complete track entries.

[▶ 1] [⋯ 1]
[▶ 2] [⋯ 2]
...
```

**Layer 3 — Management**

```
Track details

Get again
Preview
Spotify
Add to playlist
Remove
```

Playlists extend this architecture without changing the primary library interaction.

The Mini App later provides the visual browsing and continuous playback experience that Telegram chat cannot deliver comfortably.

## First implementation increment

Start with **A1 + A2 + A3 + A4**, in that order:

1. Rework the My music menu.
    
2. Replace the old list interaction with the shared five-track list.
    
3. Introduce track details and move secondary actions there.
    
4. Implement library search.
    

Do not start with Playlists.

The search capability is the missing foundation. It directly addresses the owner's original complaint and makes the library usable at 500 tracks before adding another organizational feature.

**Final product decision:** Telegram is the fast-access and management interface; the Mini App is the visual music-library and playback interface.

---

**End of specification**

**Version:** 1.0  
**Date:** 2026-11-21  
**Status:** Proposed final UX — ready for implementation breakdown and review.