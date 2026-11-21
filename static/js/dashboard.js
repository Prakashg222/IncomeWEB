document.addEventListener("DOMContentLoaded", () => {
  if (!window.Chart || !window.incomeCharts) return;
  Chart.defaults.font.family = '"DM Sans", sans-serif';
  Chart.defaults.color = "#8b96a8";
  const grid = "#edf0f5";
  const dailyCanvas = document.getElementById("dailyChart");
  if (dailyCanvas) {
    const context = dailyCanvas.getContext("2d");
    const fill = context.createLinearGradient(0, 0, 0, 230);
    fill.addColorStop(0, "rgba(80, 108, 232, .20)");
    fill.addColorStop(1, "rgba(80, 108, 232, 0)");
    new Chart(context, {
      type: "line",
      data: { labels: window.incomeCharts.dailyLabels, datasets: [{ data: window.incomeCharts.dailyValues, borderColor: "#5872e8", backgroundColor: fill, fill: true, tension: .38, borderWidth: 2, pointRadius: 2, pointHoverRadius: 5 }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (item) => ` ₹${item.parsed.y.toFixed(2)}` } } }, scales: { x: { grid: { display: false }, border: { display: false } }, y: { beginAtZero: true, grid: { color: grid }, border: { display: false }, ticks: { callback: (value) => `₹${value}` } } } }
    });
  }
  const monthlyCanvas = document.getElementById("monthlyChart");
  if (monthlyCanvas) new Chart(monthlyCanvas, {
    type: "bar",
    data: { labels: window.incomeCharts.monthLabels, datasets: [{ data: window.incomeCharts.monthValues, backgroundColor: "#897be5", hoverBackgroundColor: "#7363d5", borderRadius: 5, maxBarThickness: 28 }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (item) => ` ₹${item.parsed.y.toFixed(2)}` } } }, scales: { x: { grid: { display: false }, border: { display: false } }, y: { beginAtZero: true, grid: { color: grid }, border: { display: false }, ticks: { callback: (value) => `₹${value}` } } } }
  });
});
