/** How a repeat is named, chosen and described. The server alone expands a rule into dates (`recurrence.py`); nothing here computes an
 *  occurrence, so there is exactly one implementation of "when does this event happen". */
import { addDays } from './dates.js';
/** The choices in the "Repeats" menu. "Bi-monthly" is every two months, "quarterly" every three, a "trimester" every four. */
export const PRESETS = [
    { id: 'none', label: 'Does not repeat', rule: null },
    { id: 'daily', label: 'Every day', rule: { freq: 'daily', interval: 1 } },
    { id: 'weekdays', label: 'Every weekday (Mon–Fri)', rule: { freq: 'weekly', interval: 1, weekdays: [1, 2, 3, 4, 5] } },
    { id: 'weekly', label: 'Every week', rule: { freq: 'weekly', interval: 1 } },
    { id: 'biweekly', label: 'Every 2 weeks (bi-weekly)', rule: { freq: 'weekly', interval: 2 } },
    { id: 'monthly', label: 'Every month', rule: { freq: 'monthly', interval: 1 } },
    { id: 'bimonthly', label: 'Every 2 months (bi-monthly)', rule: { freq: 'monthly', interval: 2 } },
    { id: 'quarterly', label: 'Every 3 months (quarterly)', rule: { freq: 'monthly', interval: 3 } },
    { id: 'trimester', label: 'Every 4 months (trimester)', rule: { freq: 'monthly', interval: 4 } },
    { id: 'semiannual', label: 'Every 6 months (semi-annually)', rule: { freq: 'monthly', interval: 6 } },
    { id: 'yearly', label: 'Every year', rule: { freq: 'yearly', interval: 1 } },
    { id: 'custom', label: 'Custom…', rule: null },
];
const sameDays = (a, b) => a.length === b.length && a.every((d, i) => d === b[i]);
/** Which menu choice a stored rule corresponds to (the end of the repeat is a separate setting and plays no part). */
export function presetOf(rule) {
    if (!rule)
        return 'none';
    const days = rule.weekdays ?? [];
    const found = PRESETS.find((p) => p.rule !== null && p.rule.freq === rule.freq && p.rule.interval === rule.interval
        && sameDays(p.rule.weekdays ?? [], days) && (rule.freq !== 'monthly' || (rule.month_mode ?? 'date') === 'date'));
    return found?.id ?? 'custom';
}
/** The rule a menu choice stands for, with the end of the repeat applied. `null` for "Does not repeat" and for "Custom" (which has its own controls). */
export function ruleFromPreset(id, end) {
    const preset = PRESETS.find((p) => p.id === id);
    if (!preset?.rule)
        return null;
    const { freq, interval, weekdays } = preset.rule;
    return { freq, interval, ...(freq === 'weekly' ? { weekdays: weekdays ?? [] } : {}), ...(freq === 'monthly' ? { month_mode: 'date' } : {}), ...end };
}
export function ordinal(n) {
    const rest = n % 100;
    if (rest >= 11 && rest <= 13)
        return `${n}th`;
    return `${n}${{ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] ?? 'th'}`;
}
/** Which of the month's weekdays `day` is: 1-4, or 5 meaning "the last" (the server uses the same rule). */
export const nthWeekdayOfMonth = (day) => Math.floor((day.getDate() - 1) / 7) + 1;
export function weekdayName(isoWeekday, style = 'long', locale) {
    const base = new Date(2026, 9, 26); // a Monday
    return addDays(base, isoWeekday - 1).toLocaleDateString(locale, { weekday: style });
}
const PLURAL = { daily: ['day', 'days'], weekly: ['week', 'weeks'], monthly: ['month', 'months'], yearly: ['year', 'years'] };
/** "Every 2 weeks on Tuesday and Thursday, until 12 Dec 2026". `start` is the first occurrence, which the "on ..." part depends on. */
export function describeRule(rule, start, locale) {
    const [one, many] = PLURAL[rule.freq];
    const every = rule.interval === 1 ? (rule.freq === 'daily' ? 'Every day' : `Every ${one}`) : `Every ${rule.interval} ${many}`;
    let on = '';
    if (rule.freq === 'weekly') {
        const days = rule.weekdays && rule.weekdays.length > 0 ? rule.weekdays : [start.getDay() === 0 ? 7 : start.getDay()];
        const names = days.map((d) => weekdayName(d, 'long', locale));
        on = days.length === 5 && days.every((d, i) => d === i + 1) ? ' on weekdays' : ` on ${names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}` : names[0]}`;
    }
    else if (rule.freq === 'monthly') {
        const n = nthWeekdayOfMonth(start);
        on = rule.month_mode === 'weekday' ? ` on the ${n >= 5 ? 'last' : ordinal(n)} ${weekdayName(start.getDay() === 0 ? 7 : start.getDay(), 'long', locale)}` : ` on day ${start.getDate()}`;
    }
    else if (rule.freq === 'yearly') {
        on = ` on ${start.toLocaleDateString(locale, { month: 'long', day: 'numeric' })}`;
    }
    let end = '';
    if (rule.until)
        end = `, until ${new Date(`${rule.until}T00:00:00`).toLocaleDateString(locale, { year: 'numeric', month: 'short', day: 'numeric' })}`;
    else if (rule.count)
        end = rule.count === 1 ? ', once' : `, ${rule.count} times`;
    return `${every}${on}${end}`;
}
/** A short badge text for lists and tooltips: "Repeats weekly", "Repeats every 3 months". */
export function shortRepeat(rule) {
    const [one, many] = PLURAL[rule.freq];
    return rule.interval === 1 ? `Repeats ${rule.freq === 'daily' ? 'daily' : `every ${one}`}` : `Repeats every ${rule.interval} ${many}`;
}
//# sourceMappingURL=recurrence.js.map