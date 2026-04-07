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

      const ganttData = payload.gantt || {};
      const chartStore = (window.durumCharts = window.durumCharts || {});

      // ── Gantt-диаграмма смен ──────────────────────────────────────────────
      const ganttCanvas = document.getElementById("ganttChart");
      if (
        ganttCanvas &&
        Array.isArray(ganttData.labels) &&
        ganttData.labels.length > 0
      ) {
        if (chartStore.gantt instanceof Chart) {
          chartStore.gantt.destroy();
        }

        const minutesToHHMM = (minutes) => {
          const h = Math.floor(minutes / 60) % 24;
          const m = Math.floor(minutes) % 60;
          return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
        };

        const tooltips = ganttData.tooltips || [];

        chartStore.gantt = new Chart(ganttCanvas, {
          type: "bar",
          data: {
            labels: ganttData.labels,
            datasets: [
              {
                label: "Открытие смены",
                data: ganttData.opening_blocks,
                backgroundColor: "rgba(34, 197, 94, 0.45)",
                borderColor: "rgba(34, 197, 94, 0.9)",
                borderWidth: 1,
                borderRadius: 3,
              },
              {
                label: "Ведение смены",
                data: ganttData.operation_blocks,
                backgroundColor: "rgba(59, 130, 246, 0.35)",
                borderColor: "rgba(59, 130, 246, 0.7)",
                borderWidth: 1,
                borderRadius: 3,
              },
              {
                label: "Закрытие смены",
                data: ganttData.closing_blocks,
                backgroundColor: "rgba(239, 68, 68, 0.45)",
                borderColor: "rgba(239, 68, 68, 0.9)",
                borderWidth: 1,
                borderRadius: 3,
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
              tooltip: {
                callbacks: {
                  label(ctx) {
                    const tip = tooltips[ctx.dataIndex] || {};
                    const phases = ["opening", "operation", "closing"];
                    const text = tip[phases[ctx.datasetIndex]];
                    return text || null;
                  },
                },
              },
            },
            scales: {
              y: {
                min: 540,
                max: 1440,
                reverse: false,
                ticks: {
                  stepSize: 60,
                  callback(value) {
                    return minutesToHHMM(value);
                  },
                },
                grid: {
                  color: "rgba(145, 158, 171, 0.2)",
                },
                title: {
                  display: true,
                  text: "Время",
                  color: "#637286",
                  font: { size: 11 },
                },
              },
              x: {
                grid: {
                  color: "rgba(145, 158, 171, 0.1)",
                },
                title: {
                  display: true,
                  text: "Дата",
                  color: "#637286",
                  font: { size: 11 },
                },
              },
            },
          },
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
