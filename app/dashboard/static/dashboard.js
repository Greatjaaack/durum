(() => {
  // ── Charts ───────────────────────────────────────────────────────────────
  if (typeof Chart !== "undefined") {
    const dataNode = document.getElementById("dashboard-charts-data");
    if (dataNode) {
      let payload;
      try {
        payload = JSON.parse(dataNode.textContent || "{}");
      } catch (_error) {
        payload = {};
      }

      const residualBar = payload.residual_bar || {};
      const closeLine = payload.close_line || {};
      const chartStore = (window.durumCharts = window.durumCharts || {});

      const formatValue = (value) => {
        if (typeof value !== "number" || !Number.isFinite(value)) {
          return "";
        }
        if (Math.abs(value - Math.round(value)) < 0.001) {
          return String(Math.round(value));
        }
        return value.toFixed(1).replace(/\.0$/, "");
      };

      const valueLabelPlugin = {
        id: "valueLabelPlugin",
        afterDatasetsDraw(chart) {
          const { ctx } = chart;
          ctx.save();
          ctx.font = "600 11px SF Pro Text, Segoe UI, sans-serif";
          ctx.fillStyle = "#223349";
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";

          chart.data.datasets.forEach((dataset, datasetIndex) => {
            const meta = chart.getDatasetMeta(datasetIndex);
            if (meta.hidden) {
              return;
            }
            meta.data.forEach((point, index) => {
              const rawValue = dataset.data[index];
              const label = formatValue(Number(rawValue));
              if (!label) {
                return;
              }
              const x = point.x;
              const y = point.y - 6;
              ctx.fillText(label, x, y);
            });
          });

          ctx.restore();
        },
      };

      const barCanvas = document.getElementById("residualBarChart");
      if (barCanvas && Array.isArray(residualBar.labels) && residualBar.labels.length > 0) {
        if (chartStore.residualBar instanceof Chart) {
          chartStore.residualBar.destroy();
        }
        chartStore.residualBar = new Chart(barCanvas, {
          type: "bar",
          data: {
            labels: residualBar.labels,
            datasets: [
              {
                label: "Текущее значение",
                data: residualBar.current_values || [],
                borderWidth: 1,
                backgroundColor: "rgba(17, 59, 143, 0.86)",
                borderColor: "rgba(17, 59, 143, 1)",
              },
              {
                label: "Среднее значение",
                data: residualBar.avg_values || [],
                borderWidth: 1,
                backgroundColor: "rgba(97, 114, 134, 0.5)",
                borderColor: "rgba(97, 114, 134, 0.8)",
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            resizeDelay: 120,
            plugins: {
              legend: {
                position: "top",
              },
            },
            scales: {
              y: {
                beginAtZero: true,
                grid: {
                  color: "rgba(145, 158, 171, 0.2)",
                },
              },
            },
          },
          plugins: [valueLabelPlugin],
        });
      }

      const lineCanvas = document.getElementById("closeDurationChart");
      if (lineCanvas && Array.isArray(closeLine.labels) && closeLine.labels.length > 0) {
        if (chartStore.closeLine instanceof Chart) {
          chartStore.closeLine.destroy();
        }
        chartStore.closeLine = new Chart(lineCanvas, {
          type: "line",
          data: {
            labels: closeLine.labels,
            datasets: [
              {
                label: "Время закрытия (мин)",
                data: closeLine.values || [],
                borderWidth: 2,
                fill: false,
                borderColor: "rgba(180, 27, 27, 0.9)",
                backgroundColor: "rgba(180, 27, 27, 0.2)",
                tension: 0.25,
                pointRadius: 4,
                pointHoverRadius: 5,
                pointBackgroundColor: "rgba(180, 27, 27, 1)",
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            resizeDelay: 120,
            plugins: {
              legend: {
                position: "top",
              },
            },
            scales: {
              y: {
                beginAtZero: true,
                grid: {
                  color: "rgba(145, 158, 171, 0.2)",
                },
              },
            },
          },
          plugins: [valueLabelPlugin],
        });
      }
    }
  }

  // ── Lightbox ─────────────────────────────────────────────────────────────
  const lightbox = document.getElementById("lightbox");
  if (!lightbox) return;

  const lightboxImg = lightbox.querySelector(".lightbox__img");
  const lightboxLabel = lightbox.querySelector(".lightbox__label");
  const lightboxClose = lightbox.querySelector(".lightbox__close");
  const lightboxBackdrop = lightbox.querySelector(".lightbox__backdrop");

  function openLightbox(src, label) {
    lightboxImg.src = src;
    lightboxImg.alt = label || "";
    lightboxLabel.textContent = label || "";
    lightbox.classList.add("is-open");
    document.body.style.overflow = "hidden";
  }

  function closeLightbox() {
    lightbox.classList.remove("is-open");
    document.body.style.overflow = "";
    lightboxImg.src = "";
  }

  document.querySelectorAll(".js-lightbox").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      openLightbox(link.href, link.dataset.label || "");
    });
  });

  lightboxClose.addEventListener("click", closeLightbox);
  lightboxBackdrop.addEventListener("click", closeLightbox);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && lightbox.classList.contains("is-open")) {
      closeLightbox();
    }
  });
})();
