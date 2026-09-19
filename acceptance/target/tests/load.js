import http from "k6/http";
import { check } from "k6";

export const options = {
  vus: 1,
  iterations: 3,
  thresholds: {
    http_req_failed: ["rate<0.01"],
  },
};

export default function () {
  const response = http.get(`${__ENV.TARGET_URL || "http://127.0.0.1:8000"}/answer?value=hello`);
  check(response, { "answer endpoint is healthy": (value) => value.status === 200 });
}
