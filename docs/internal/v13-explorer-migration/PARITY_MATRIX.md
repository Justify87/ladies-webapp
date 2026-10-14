# V13 Explorer Feature Parity Matrix

Allowed states: `NOT STARTED`, `IN PROGRESS`, `IMPLEMENTED`, `VERIFIED`.

| Issue | Capability | Status | Evidence / next verification |
|---|---|---|---|
| #24 | Foundation, shell, state architecture | VERIFIED | V13-only shell, store/reducer/router/API/adapters present |
| #25 | V13 only Explorer UI | VERIFIED | `index.html` loads V13 modules only |
| #26 | No production fixtures/dummies | IMPLEMENTED | No prototype fixture runtime found; manual verification pending |
| #27 | Search + production filters | IMPLEMENTED | backend-config driven UI/payload mapping present |
| #28 | Search preview | IMPLEMENTED | `/api/search-preview` flow present |
| #29 | Swipe | IMPLEMENTED | productive search/profile data flow present |
| #30 | Grid | IMPLEMENTED | productive result/media/status flow present |
| #31 | Up-scroll profile details | IMPLEMENTED | shared profile details in swipe scroll; manual verification pending |
| #32 | Like | IMPLEMENTED | state persistence path present; manual verification pending |
| #33 | Dislike | IMPLEMENTED | state persistence path present; manual verification pending |
| #34 | Favorite | IMPLEMENTED | state persistence path present; manual verification pending |
| #35 | Undo | IMPLEMENTED | persisted-state/queue restoration path present; manual verification pending |
| #36 | Favorites/Likes/Dislikes lists | IMPLEMENTED | list screen/API flow present; manual verification pending |
| #37 | Shared profile implementation | VERIFIED | swipe + standalone use common profile renderer |
| #38 | Profile info | IMPLEMENTED | media/identity/info renderer present |
| #39 | Profile offer | IMPLEMENTED | services/traits/hours/prices present |
| #40 | Profile analysis | IMPLEMENTED | real backend analytics only; no hardcoded fake score |
| #41 | My Data persistence | IMPLEMENTED | person-state/encounter UI path present; manual reload verification pending |
| #42 | Encounters | IMPLEMENTED | create/update path present; manual verification pending |
| #43 | Price lists | IMPLEMENTED | manual/profile price-list UI + API present |
| #44 | Profile history | IMPLEMENTED | timeline/listings/snapshot comparison path present |
| #45 | Merge/split | IMPLEMENTED | profile overflow actions/API paths present |
| #46 | Status checks | IMPLEMENTED | single + bulk status effects present |
| #47 | Global history/statistics | IMPLEMENTED | Settings subroute/dashboard/history present |
| #48 | WhatsApp connect/QR | IMPLEMENTED | real status/connect/QR/disconnect/logout endpoints used |
| #49 | Message list | IMPLEMENTED | all/unread/unassigned/search metadata present; manual verification pending |
| #50 | Messaging live updates | IMPLEMENTED | EventSource-based SSE path present |
| #51 | Full chat | IMPLEMENTED | pagination/send/status/presence/navigation present; manual verification pending |
| #52 | Chat media + voice | IMPLEMENTED | upload/playback/MediaRecorder paths present |
| #53 | Reply/reaction/edit/delete/retry | IMPLEMENTED | actions/API paths present |
| #54 | View Once | IMPLEMENTED | dedicated consume link; no image prefetch contract |
| #55 | Contact assignment | IMPLEMENTED | candidates/assign/create-person paths present |
| #56 | Pricing | IMPLEMENTED | inline signal/proposal/edit/accept/reject/current/history present |
| #57 | Settings | IMPLEMENTED | productive settings groups present; manual verification pending |
| #58 | Archive/storage | IMPLEMENTED | storage/archive/cleanup paths present |
| #59 | Visual analysis | IMPLEMENTED | automatic/manual persisted analysis paths present |
| #60 | Legacy Explorer JS/CSS removed | VERIFIED | obsolete files deleted from branch |
| #61 | No legacy Explorer assets loaded | VERIFIED | final template references only V13 Explorer runtime assets |
| #62 | Manager unchanged | VERIFIED | Explorer migration does not replace Manager runtime/UI |
| #67 | Release contains V13 assets | IMPLEMENTED | nested package-data present; built archive verification pending |
| #68 | Fresh release install/start | NOT STARTED | perform after release verification |

Items #63–#66 from the original DoD were retired because browser-smoke, E2E, visual-regression and heavyweight CI gates are intentionally no longer part of the migration contract.
