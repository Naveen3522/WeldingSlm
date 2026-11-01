// How times are shown: the date and the time with AM or PM, e.g. "25 Oct, 10:42 AM".

export function when(seconds) {
  const d = new Date(seconds * 1000);
  const date = d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  const time = d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: true });
  return `${date}, ${time}`;
}
