# TurnToPage Working Plan

## Purpose

This document is the working plan for TurnToPage so development can proceed in a deliberate order instead of ad hoc feature work.

Primary product goal:

- Build a desktop application for tracking a personal gamebook collection.
- Search and browse gamebooks.org data.
- Track `have`, `want`, and `missing` statuses locally.
- Show gaps in series and help prioritize what to find next.

## Current State

## Progress Snapshot (2026-04-01)

Overall completion estimate: **78%**

Phase progress:

- Phase 1 (Core usability): **95%**
- Phase 2 (Better search and browse): **90%**
- Phase 3 (Series completion experience): **100%**
- Phase 4 (Rich catalog features): **30%**

Focus now:

- Finish remaining Phase 3 gaps (`missing only`, core-vs-omnibus separation, stronger completion cards/suggestions)
- Start catalog-first GUI path once Phase 3 baseline is complete

Implemented:

- `gamebooks_client/api.py`
  - Search gamebooks.org
  - Fetch item details
  - Fetch series details
- `gamebooks_client/collection.py`
  - Local SQLite storage for collection statuses
- `gamebooks_client/catalog.py`
  - Local SQLite catalog store for crawled item and series data
- `gamebooks_client/crawler.py`
  - Resumable, rate-limited crawler for item and series pages
- `gamebooks_client/cli.py`
  - Search, item, series, collection status, and crawl commands
- `gamebooks_client/gui.py`
  - Desktop GUI with Search, My Collection, and Series Gap Report tabs

Current UX already available:

- Search for books and series
- Mark books as `have`, `want`, or `missing`
- Browse tracked collection entries
- Open item details with metadata and cover image
- Open series gap report and bulk-mark statuses

## Product Direction

Short-term direction:

- Keep improving the frontend and local workflow.
- Continue using live site scraping where needed.
- Use the crawler to build a local catalog snapshot.
- Wait for response from the site owner before assuming API or direct DB access.

Long-term direction:

- Prefer distributing a snapshot database from GitHub releases instead of asking every user to crawl the site.
- Use live scraping only as fallback or for data refreshes.

## Data Strategy

### Confirmed facts

- The public `gamebooks.sql` file in the Geeby-Deeby repo appears to be a sample/test dataset, not the full production catalog.
- It is not sufficient as the primary source for a complete TurnToPage catalog.
- The current crawler can build a fuller local catalog by traversing item and series IDs.

### Pending decision

Waiting on site owner response about:

- Permission to crawl at scale
- Availability of an API
- Availability of direct database exports or structured feeds

### Working assumption until then

- Continue frontend work now.
- Keep the crawler available for your own catalog-building only.
- Plan the app so it can later switch between:
  - live scrape source
  - local catalog snapshot
  - future API source

## Frontend Priorities

### Phase 1: Core usability

Goal:

- Make the current desktop GUI faster and easier to use for day-to-day collection tracking.

Tasks:

- [x] Add a Dashboard tab
- [x] Add collection summary counts (`have`, `want`, `missing`, `unknown`)
- [x] Add recently changed items panel
- [x] Add quick actions from dashboard
- [x] Add keyboard shortcuts for `have`, `want`, `missing`
- [x] Add better empty states and loading states
- [x] Add clearer success/error notifications
- [x] Add status legend in the GUI

Definition of done:

- A user can open the app and immediately understand collection progress and what to do next.

### Phase 2: Better search and browse

Goal:

- Make it easy to find items, inspect them, and act on them quickly.

Tasks:

- [x] Add reusable filter bar components
- [x] Add filtering by status in Search results
- [x] Add local-text filtering within results lists
- [x] Add sort controls for title, ID, series count, and status
- [x] Add bulk actions in Search and Series tabs
- [x] Add friendlier double-click behavior everywhere
- [ ] Add optional local-catalog-first search path when a catalog DB exists

Definition of done:

- Search and browse flows feel fast and require minimal clicking.

### Phase 3: Series completion experience

Goal:

- Make series tracking the center of the product.

Tasks:

- [x] Add a dedicated Series detail view
- [x] Show ordered checklist of books in series
- [x] Show completion percentage
- [x] Add `missing only` toggle
- [x] Separate core books from omnibus/collection entries
- [x] Add series progress cards to dashboard
- [x] Add "closest to completion" suggestions

Definition of done:

- A user can quickly see which series are nearly complete and exactly what is still missing.

### Phase 4: Rich catalog features

Goal:

- Use structured data to make the app genuinely useful beyond simple lookup.

Tasks:

- [ ] Add creator browsing (authors, illustrators)
- [ ] Add publisher and language filters
- [ ] Add genre/category filters
- [ ] Add related-item views
- [ ] Add edition-focused browsing
- [x] Add recommendations based on owned items
- [x] Add saved searches or saved filter presets

Definition of done:

- The app supports discovery, not just collection logging.

## Proposed UX Features Backlog

### Dashboard

- Total tracked items count
- Have / Want / Missing totals
- Series completion summary
- Recently marked items
- Snapshot age / data-source status
- Quick launch actions

### Search tab

- Search box with live filtering of displayed results
- Type column (`Book` / `Series`)
- Books-in-series count for series results
- Double-click series opens series view
- Double-click book opens item details
- Status-aware filtering
- Bulk status updates

### Collection tab

- Search within collection
- Sort by updated date, title, status
- [x] Group by status or series
- Quick remove and undo
- Quick jump to series for selected item

### Series tab

- Core books vs collections sections
- Completion progress display
- Missing-only filter
- Bulk mark selected books
- Open item detail from row
- [x] “Mark unknown as missing” helper

### Item detail view

- Cleaner layout for structured metadata
- Better editions presentation
- Related links grouped by type
- Creator chips that open searches
- Series navigation controls (`previous` / `next` within series when known)

## Technical Backlog

### Quality and release

- [x] Add tests for new collection methods (`series_progress`, `suggestions`, presets)
- [x] Rebuild EXE after feature updates

### Frontend architecture

- Refactor common list/table behavior into reusable helpers
- Separate GUI state management from view widgets where practical
- Add local catalog integration layer for GUI

### Catalog integration

- Read from `turntopage_catalog.db` when present
- Fall back to live scrape when absent
- Add metadata table for snapshot version, source, crawl date, and attribution
- Add `catalog-info` CLI command

### Packaging and distribution

- Add release process for shipping GUI builds
- Add GitHub release flow for catalog snapshots
- Add versioned catalog filenames and checksums

## Immediate Next Candidates

Pick one of these next:

1. Dashboard tab
2. Reusable filter/search bar across tabs
3. Series detail/completion experience improvements
4. Local catalog integration into GUI
5. Catalog metadata and snapshot info display

## Recommended Order

Recommended implementation order:

1. Dashboard tab
2. Search and Collection filter improvements
3. Series completion UX improvements
4. Local catalog integration into GUI
5. Catalog metadata / snapshot info
6. Discovery features (authors, categories, recommendations)

## Open Questions

- Will the site owner allow crawling for catalog mirroring?
- Is there an API or structured export available?
- Should the app eventually default to local catalog search only, with live scrape as fallback?
- How much emphasis should be placed on editions vs base items in the UI?
- Should users be able to track duplicates/extras in addition to single ownership states?

## Notes

- The app should stay useful even if live site access is unavailable.
- The GUI should optimize for fast personal collection management, not just raw data display.
- Series completion is likely the strongest product differentiator and should remain central to the UX.