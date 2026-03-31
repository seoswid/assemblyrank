const state = {
  rankings: [],
  visibleRankings: [],
  selectedKey: null,
  loading: false,
};

const PARTY_LOGO_STYLES = {
  "더불어민주당": { bg: "#0f6bdc", fg: "#ffffff", border: "#0a4fa6" },
  "국민의힘": { bg: "#e61e2b", fg: "#ffffff", border: "#b3151f" },
  "조국혁신당": { bg: "#143d8f", fg: "#ffffff", border: "#102e6a" },
  "개혁신당": { bg: "#ff7210", fg: "#ffffff", border: "#d55b07" },
  "국민의미래": { bg: "#ff5a66", fg: "#ffffff", border: "#d74450" },
  "더불어민주연합": { bg: "#2a8cff", fg: "#ffffff", border: "#1467c7" },
  "진보당": { bg: "#d81f26", fg: "#ffffff", border: "#ab171c" },
  "새로운미래": { bg: "#18a999", fg: "#ffffff", border: "#0f7d71" },
  "기본소득당": { bg: "#00a6ff", fg: "#ffffff", border: "#0a7ec0" },
};

const elements = {
  loadButton: document.querySelector("#loadButton"),
  refreshVotesButton: document.querySelector("#refreshVotesButton"),
  searchInput: document.querySelector("#searchInput"),
  partyFilter: document.querySelector("#partyFilter"),
  sortSelect: document.querySelector("#sortSelect"),
  rankingBody: document.querySelector("#rankingBody"),
  detailCard: document.querySelector("#detailCard"),
  memberCount: document.querySelector("#memberCount"),
  avgAttendance: document.querySelector("#avgAttendance"),
  totalProposals: document.querySelector("#totalProposals"),
  topProposer: document.querySelector("#topProposer"),
  statusMessage: document.querySelector("#statusMessage"),
  statusMeta: document.querySelector("#statusMeta"),
  resultCount: document.querySelector("#resultCount"),
  assemblyLabel: document.querySelector("#assemblyLabel"),
};

wireEvents();
boot();

function wireEvents() {
  elements.loadButton.addEventListener("click", () => refreshDatabase());
  elements.refreshVotesButton.addEventListener("click", () => fetchDashboard());
  elements.searchInput.addEventListener("input", renderRankings);
  elements.partyFilter.addEventListener("change", renderRankings);
  elements.sortSelect.addEventListener("change", renderRankings);
}

async function boot() {
  updateStatus("데이터베이스 연결을 준비하는 중입니다.", "로컬 서버 API를 확인합니다.", 8);
  await fetchDashboard({ silentNotReady: true });
}

async function fetchDashboard(options = {}) {
  if (state.loading) {
    return;
  }

  state.loading = true;
  setButtonsDisabled(true);

  try {
    updateStatus("SQLite 데이터베이스에서 랭킹을 읽는 중입니다.", "저장된 최신 집계 결과를 불러옵니다.", 22);
    const response = await fetch("/api/dashboard");
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.error || "대시보드 데이터를 읽지 못했습니다.");
    }

    hydrateDashboard(payload);
    updateStatus(
      "",
      `데이터 동기화 시간: ${payload.meta.last_synced_at || "없음"}`,
      100,
    );
  } catch (error) {
    if (options.silentNotReady) {
      updateStatus(
        "아직 데이터베이스가 비어 있습니다.",
        "좌측 버튼으로 원본 데이터를 업데이트하면 결과 DB도 함께 갱신됩니다.",
        0,
      );
      elements.rankingBody.innerHTML = `<tr><td colspan="9" class="empty">아직 저장된 랭킹 데이터가 없습니다. "데이터 업데이트"를 눌러 주세요.</td></tr>`;
      return;
    }
    handleFatalError(error);
  } finally {
    state.loading = false;
    setButtonsDisabled(false);
  }
}

async function refreshDatabase() {
  if (state.loading) {
    return;
  }

  state.loading = true;
  setButtonsDisabled(true);

  try {
    updateStatus("원본 데이터를 업데이트하는 중입니다.", "국회의원, 의안, 표결 원본 DB를 갱신한 뒤 결과 DB도 다시 만듭니다.", 10);
    const response = await fetch("/api/refresh", { method: "POST" });
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.error || "데이터 업데이트에 실패했습니다.");
    }

    hydrateDashboard(payload);
    updateStatus(
      "",
      `데이터 동기화 시간: ${payload.meta.last_synced_at || "없음"}`,
      100,
    );
  } catch (error) {
    handleFatalError(error);
  } finally {
    state.loading = false;
    setButtonsDisabled(false);
  }
}

function hydrateDashboard(payload) {
  state.rankings = payload.rankings || [];
  populatePartyFilter(state.rankings);
  elements.assemblyLabel.textContent = payload.meta.assembly_label || "제22대";
  renderRankings();
  renderSummary(payload.summary || {});

  if (!state.selectedKey && state.visibleRankings[0]) {
    state.selectedKey = state.visibleRankings[0].key;
  }
  renderDetails();
}

function renderRankings() {
  const query = elements.searchInput.value.trim().toLowerCase();
  const selectedParty = elements.partyFilter.value;
  const sortKey = elements.sortSelect.value;

  const filtered = state.rankings.filter((entry) => {
    const haystack = [
      entry.name,
      entry.current_party,
      ...(entry.party_history || []),
      entry.current_district,
      ...(entry.district_history || []),
      entry.committee,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();

    const matchesQuery = !query || haystack.includes(query);
    const matchesParty = !selectedParty || entry.current_party === selectedParty;
    return matchesQuery && matchesParty;
  });

  const sorted = [...filtered].sort((left, right) => compareRanking(left, right, sortKey));
  state.visibleRankings = sorted;

  if (!sorted.some((entry) => entry.key === state.selectedKey) && sorted[0]) {
    state.selectedKey = sorted[0].key;
  }

  elements.resultCount.textContent = `${sorted.length.toLocaleString("ko-KR")}명`;

  if (sorted.length === 0) {
    elements.rankingBody.innerHTML = `<tr><td colspan="8" class="empty">조건에 맞는 의원이 없습니다.</td></tr>`;
    renderDetails();
    return;
  }

  elements.rankingBody.innerHTML = sorted.map((entry) => {
    const selectedClass = entry.key === state.selectedKey ? "is-selected" : "";
    const attendanceClass = entry.attendance_rate >= 90 ? "metric-up" : entry.attendance_rate < 70 ? "metric-warn" : "";
    const voteParticipationRate = getVoteParticipationRate(entry);
    return `
      <tr class="${selectedClass}" data-key="${entry.key}">
        <td><span class="rank-badge">${entry.rank}</span></td>
        <td>
          <div class="name-cell">
            <img class="avatar" src="${entry.photo_url || ""}" alt="${entry.name}">
            <div>
              <strong>${entry.name}</strong>
              <span>${entry.reelection || "-"}</span>
            </div>
          </div>
        </td>
        <td>${renderPartyCell(entry)}</td>
        <td class="${attendanceClass}">${formatPercent(entry.attendance_rate)}</td>
        <td>${formatPercent(voteParticipationRate)}</td>
        <td>${entry.proposal_count.toLocaleString("ko-KR")}건</td>
        <td>${entry.processed_proposal_count.toLocaleString("ko-KR")}건</td>
        <td><strong>${entry.score.toFixed(1)}</strong></td>
      </tr>
    `;
  }).join("");

  [...elements.rankingBody.querySelectorAll("tr[data-key]")].forEach((row) => {
    row.addEventListener("click", () => {
      state.selectedKey = row.dataset.key;
      renderRankings();
      renderDetails();
    });
  });
}

function renderSummary(summary) {
  elements.memberCount.textContent = `${summary.member_count?.toLocaleString("ko-KR") || 0}명`;
  elements.avgAttendance.textContent = formatPercent(summary.average_attendance_rate || 0);
  elements.totalProposals.textContent = `${summary.total_proposals?.toLocaleString("ko-KR") || 0}건`;
  elements.topProposer.textContent = summary.top_proposer_name
    ? `${summary.top_proposer_name} (${summary.top_proposal_count.toLocaleString("ko-KR")}건)`
    : "-";
}

function renderDetails() {
  const selected = state.visibleRankings.find((entry) => entry.key === state.selectedKey) || state.rankings.find((entry) => entry.key === state.selectedKey);
  if (!selected) {
    elements.detailCard.innerHTML = `<div class="detail-card__placeholder">왼쪽 랭킹에서 의원을 선택하면 상세 정보가 표시됩니다.</div>`;
    return;
  }

  const proposals = selected.latest_proposals?.length
    ? selected.latest_proposals.map((item) => `
        <div class="detail-item">
          <strong>${item.bill_name}</strong>
          <span>의안번호 ${item.bill_no || "-"} · 제안일 ${item.proposed_date || "-"}</span>
          <span>상태 ${item.result || "-"}</span>
          ${item.link_url ? `<a href="${item.link_url}" target="_blank" rel="noreferrer">의안 상세 보기</a>` : ""}
        </div>
      `).join("")
    : `<div class="detail-item"><span>저장된 대표발의 이력이 없습니다.</span></div>`;

  const votes = selected.latest_votes?.length
    ? selected.latest_votes.map((item) => `
        <div class="detail-item">
          <strong>${item.bill_name}</strong>
          <span>${formatVoteDate(item.vote_date)} · ${item.result_vote_mod}</span>
          ${item.link_url ? `<a href="${item.link_url}" target="_blank" rel="noreferrer">관련 의안 보기</a>` : ""}
        </div>
      `).join("")
    : `<div class="detail-item"><span>저장된 표결 이력이 없습니다.</span></div>`;
  const voteParticipationRate = getVoteParticipationRate(selected);

  elements.detailCard.innerHTML = `
    <div class="detail-card__header">
      <img class="avatar" src="${selected.photo_url || ""}" alt="${selected.name}">
      <div>
        <h2 class="detail-card__title">${selected.name}</h2>
        <div class="detail-card__sub">${renderPartyVisual(selected.current_party, "large")} · ${selected.current_district || "지역구 정보 없음"}</div>
        ${renderPartyHistory(selected, "detail")}
        ${renderDistrictHistory(selected, "detail")}
      </div>
    </div>

    <div class="detail-card__grid">
      <div class="detail-metric">
        <span>종합점수</span>
        <strong>${selected.score.toFixed(1)}점</strong>
      </div>
      <div class="detail-metric">
        <span>출석률</span>
        <strong>${formatPercent(selected.attendance_rate)}</strong>
      </div>
      <div class="detail-metric">
        <span>표결 참여율</span>
        <strong>${formatPercent(voteParticipationRate)} (${selected.attended_vote_count.toLocaleString("ko-KR")} / ${selected.total_vote_count.toLocaleString("ko-KR")})</strong>
      </div>
      <div class="detail-metric">
        <span>대표발의</span>
        <strong>${selected.proposal_count.toLocaleString("ko-KR")}건</strong>
      </div>
      <div class="detail-metric">
        <span>처리의안</span>
        <strong>${selected.processed_proposal_count.toLocaleString("ko-KR")}건</strong>
      </div>
      <div class="detail-metric">
        <span>표결 성향</span>
        <strong>찬성 ${selected.yes_count} · 반대 ${selected.no_count} · 기권 ${selected.abstain_count} · 불참 ${selected.absent_count}</strong>
      </div>
    </div>

    <div class="detail-section">
      <h3>기본 정보</h3>
      <div class="detail-list">
        <div class="detail-item">
          <strong>소속 위원회</strong>
          <span>${selected.committee || "-"}</span>
        </div>
        <div class="detail-item">
          <strong>연락처</strong>
          <span>${selected.phone || "-"} · ${selected.email || "-"}</span>
        </div>
        <div class="detail-item">
          <strong>홈페이지</strong>
          ${selected.homepage_url ? `<a href="${selected.homepage_url}" target="_blank" rel="noreferrer">${selected.homepage_url}</a>` : `<span>-</span>`}
        </div>
      </div>
    </div>

    <div class="detail-section">
      <h3>최근 대표발의 이력</h3>
      <div class="detail-list">${proposals}</div>
    </div>

    <div class="detail-section">
      <h3>최근 표결 이력</h3>
      <div class="detail-list">${votes}</div>
    </div>
  `;
}

function populatePartyFilter(rankings) {
  const currentValue = elements.partyFilter.value;
  const parties = [...new Set(rankings.map((item) => item.current_party).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ko"));
  elements.partyFilter.innerHTML = `<option value="">전체</option>${parties.map((party) => `<option value="${party}">${party}</option>`).join("")}`;
  if (parties.includes(currentValue)) {
    elements.partyFilter.value = currentValue;
  }
}

function renderPartyCell(entry) {
  const currentParty = entry.current_party || entry.party || "-";
  return `
    <div class="party-cell">
      ${renderPartyVisual(currentParty, "small")}
      ${renderPartyHistory(entry, "table")}
    </div>
  `;
}

function renderDistrictCell(entry) {
  const currentDistrict = entry.current_district || entry.district || "-";
  return `
    <div class="party-cell">
      <span>${currentDistrict}</span>
      ${renderDistrictHistory(entry, "table")}
    </div>
  `;
}

function renderPartyHistory(entry, variant) {
  const history = entry.party_history || [];
  if (history.length === 0) {
    return "";
  }
  const className = variant === "detail" ? "party-history party-history--detail" : "party-history";
  const summaryLabel = variant === "detail" ? "이전 정당 보기" : "이전";
  return `
    <details class="${className}">
      <summary>${summaryLabel}</summary>
      <div class="party-history__list">${history.join(" → ")}</div>
    </details>
  `;
}

function renderDistrictHistory(entry, variant) {
  const history = entry.district_history || [];
  if (history.length === 0) {
    return "";
  }
  const className = variant === "detail" ? "party-history party-history--detail" : "party-history";
  const summaryLabel = variant === "detail" ? "이전 지역구 보기" : "이전";
  return `
    <details class="${className}">
      <summary>${summaryLabel}</summary>
      <div class="party-history__list">${history.join(" → ")}</div>
    </details>
  `;
}

function renderPartyVisual(partyName, size = "small") {
  const label = partyName || "-";
  const src = createPartyLogoDataUri(label);
  const className = size === "large" ? "party-logo party-logo--large" : "party-logo";
  return `<img class="${className}" src="${src}" alt="${label} 로고">`;
}

function createPartyLogoDataUri(partyName) {
  const style = PARTY_LOGO_STYLES[partyName] || {
    bg: "#6a5640",
    fg: "#ffffff",
    border: "#4a3521",
  };
  const width = 220;
  const height = 52;
  const safeText = escapeXml(partyName);
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
      <rect x="1" y="1" width="${width - 2}" height="${height - 2}" rx="16" fill="${style.bg}" stroke="${style.border}" stroke-width="2"/>
      <text x="${width / 2}" y="33" text-anchor="middle" font-size="24" font-family="IBM Plex Sans KR, Pretendard, sans-serif" font-weight="700" fill="${style.fg}">${safeText}</text>
    </svg>
  `.trim();
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function escapeXml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function compareRanking(left, right, sortKey) {
  if (sortKey === "name") {
    return left.name.localeCompare(right.name, "ko");
  }

  const valueMap = {
    score: "score",
    attendanceRate: "attendance_rate",
    proposalCount: "proposal_count",
    processedProposalCount: "processed_proposal_count",
    voteCount: "attended_vote_count",
  };
  const mapped = valueMap[sortKey] || "score";
  return (right[mapped] ?? 0) - (left[mapped] ?? 0) || right.score - left.score;
}

function formatPercent(value) {
  return `${Number(value || 0).toFixed(1)}%`;
}

function getVoteParticipationRate(entry) {
  const total = Number(entry.total_vote_count || 0);
  const attended = Number(entry.attended_vote_count || 0);
  if (!total) {
    return 0;
  }
  return (attended / total) * 100;
}

function formatVoteDate(value) {
  const text = String(value || "").replace(/\s+/g, "");
  if (text.length < 8) {
    return value || "-";
  }
  return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
}

function updateStatus(message, meta, progress) {
  elements.statusMessage.textContent = message;
  elements.statusMeta.textContent = meta;
}

function handleFatalError(error) {
  console.error(error);
  updateStatus("문제가 발생했습니다.", error.message || "알 수 없는 오류입니다.", 100);
  elements.rankingBody.innerHTML = `<tr><td colspan="9" class="empty">${error.message || "오류가 발생했습니다."}</td></tr>`;
  elements.detailCard.innerHTML = `<div class="detail-card__placeholder">오류가 해결되면 다시 시도해 주세요.</div>`;
}

function setButtonsDisabled(disabled) {
  elements.loadButton.disabled = disabled;
  elements.refreshVotesButton.disabled = disabled;
}
