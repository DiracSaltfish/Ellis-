const shanghaiFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Shanghai',
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

type ShanghaiClock = {
  weekday: number
  minuteOfDay: number
}

function shanghaiClock(now = new Date()): ShanghaiClock {
  try {
    const parts = shanghaiFormatter.formatToParts(now)
    const values = new Map(parts.map((part) => [part.type, part.value]))
    const hour = Number(values.get('hour') || '0')
    const minute = Number(values.get('minute') || '0')
    return {
      weekday: weekdayNumber(values.get('weekday') || ''),
      minuteOfDay: hour * 60 + minute,
    }
  } catch {
    return {
      weekday: now.getDay(),
      minuteOfDay: now.getHours() * 60 + now.getMinutes(),
    }
  }
}

function weekdayNumber(value: string) {
  switch (value.toLowerCase()) {
    case 'sun':
      return 0
    case 'mon':
      return 1
    case 'tue':
      return 2
    case 'wed':
      return 3
    case 'thu':
      return 4
    case 'fri':
      return 5
    case 'sat':
      return 6
    default:
      return new Date().getDay()
  }
}

function inShanghaiWeekdayWindow(startMinute: number, endMinute: number, now = new Date()) {
  const clock = shanghaiClock(now)
  if (clock.weekday === 0 || clock.weekday === 6) return false
  return clock.minuteOfDay >= startMinute && clock.minuteOfDay <= endMinute
}

export function isPrimaryAutoRefreshTime(now = new Date()) {
  return inShanghaiWeekdayWindow(9 * 60 + 15, 15 * 60, now)
}

export function isMinuteHistoryAutoRefreshTime(now = new Date()) {
  return inShanghaiWeekdayWindow(9 * 60 + 30, 15 * 60, now)
}
