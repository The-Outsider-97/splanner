import { minutesSinceMidnight, parseInstant, startOfDay, addDays } from './dates.js';
/** Minutes the timed event covers on `day`, or null when it does not touch that day. A midnight crossing is cut at the day's edges. */
export function spanOnDay(event, day) {
    const dayStart = startOfDay(day);
    const dayEnd = startOfDay(addDays(day, 1));
    const start = parseInstant(event.start_at);
    const end = parseInstant(event.end_at);
    if (!(start.getTime() < dayEnd.getTime() && end.getTime() > dayStart.getTime()))
        return null;
    const from = start.getTime() <= dayStart.getTime() ? 0 : minutesSinceMidnight(start);
    const to = end.getTime() >= dayEnd.getTime() ? 24 * 60 : minutesSinceMidnight(end);
    return { start: from, end: Math.max(to, from + 1) };
}
/**
 * Greedy column packing: events are grouped into clusters of transitively overlapping events; inside a cluster each event takes the first
 * column that is free at its start, and every event in the cluster shares the cluster's column count. `windowStart`/`windowEnd` (minutes)
 * clip what is visible; events outside it are dropped, and `minLength` keeps very short events tall enough to click.
 */
export function layoutColumns(spans, windowStart = 0, windowEnd = 24 * 60, minLength = 20) {
    const visible = spans
        .filter((s) => s.end > windowStart && s.start < windowEnd)
        .map((s) => ({ item: s.item, rawStart: s.start, rawEnd: s.end, start: Math.max(s.start, windowStart), end: Math.min(Math.max(s.end, s.start + minLength), windowEnd) }))
        .sort((a, b) => a.start - b.start || b.end - a.end);
    const placed = [];
    let cluster = [];
    let clusterEnd = -1;
    let columnEnds = [];
    const flush = () => {
        const columns = Math.max(1, columnEnds.length);
        for (const { entry, column } of cluster) {
            placed.push({ item: entry.item, start: entry.start, end: entry.end, column, columns, clippedTop: entry.rawStart < windowStart, clippedBottom: entry.rawEnd > windowEnd });
        }
        cluster = [];
        columnEnds = [];
        clusterEnd = -1;
    };
    for (const entry of visible) {
        if (cluster.length > 0 && entry.start >= clusterEnd)
            flush();
        let column = columnEnds.findIndex((end) => end <= entry.start);
        if (column === -1) {
            column = columnEnds.length;
            columnEnds.push(entry.end);
        }
        else
            columnEnds[column] = entry.end;
        cluster.push({ entry, column });
        clusterEnd = Math.max(clusterEnd, entry.end);
    }
    flush();
    return placed;
}
/** How many timed events on `day` lie completely outside the visible window (so the grid can say so instead of hiding them silently). */
export function hiddenOutside(events, day, windowStart, windowEnd) {
    let count = 0;
    for (const event of events) {
        if (event.all_day)
            continue;
        const span = spanOnDay(event, day);
        if (span && (span.end <= windowStart || span.start >= windowEnd))
            count += 1;
    }
    return count;
}
//# sourceMappingURL=layout.js.map