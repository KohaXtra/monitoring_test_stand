// Фоновая нагрузка для демо: постоянный поток ~20 RPS через nginx.
// Запуск: docker compose --profile load up -d k6
import http from "k6/http";

export const options = {
  scenarios: {
    steady: {
      executor: "constant-arrival-rate",
      rate: 20,
      timeUnit: "1s",
      duration: "8h",
      preAllocatedVUs: 20,
      maxVUs: 200,
    },
  },
};

const BASE = __ENV.BASE_URL || "http://nginx";

export default function () {
  const r = Math.random();
  if (r < 0.6) {
    http.get(`${BASE}/items/${Math.floor(Math.random() * 1000)}`);
  } else if (r < 0.8) {
    http.get(`${BASE}/hello/user${Math.floor(Math.random() * 50)}`);
  } else if (r < 0.9) {
    http.get(`${BASE}/`, { headers: { Host: "api.learnnginx.com" } });
  } else if (r < 0.98) {
    http.get(`${BASE}/`, { headers: { Host: "docs.learnnginx.com" } });
  } else {
    http.get(`${BASE}/does-not-exist`);
  }
}
