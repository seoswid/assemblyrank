const INITIAL_LIMIT = 20;

const state = {
  rankings: [],
  visibleRankings: [],
  selectedKey: null,
  loading: false,
  detailLoadingKeys: {},
  showAllRankings: false,
  statusDelayTimer: null,
  meta: {},
};

const PARTY_LOGO_STYLES = {
  "더불어민주당": { bg: "#0f6bdc", fg: "#ffffff", border: "#0a4fa6" },
  "국민의힘": { bg: "#e61e2b", fg: "#ffffff", border: "#b3151f" },
  "조국혁신당": { bg: "#143d8f", fg: "#ffffff", border: "#102e6a" },
  "개혁신당": { bg: "#ff7210", fg: "#ffffff", border: "#d55b07" },
  "기본소득당": { bg: "#00a6ff", fg: "#ffffff", border: "#0a7ec0" },
  "진보당": { bg: "#d81f26", fg: "#ffffff", border: "#ab171c" },
};

const elements = {
  loadButton: document.querySelector("#loadButton"),
  refreshVotesButton: document.querySelector("#refreshVotesButton"),
  districtFocusButton: document.querySelector("#districtFocusButton"),
  rankingsFocusButton: document.querySelector("#rankingsFocusButton"),
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
  lastUpdatedText: document.querySelector("#lastUpdatedText"),
  showMoreButton: document.querySelector("#showMoreButton"),
  rankingSection: document.querySelector("#rankingSection"),
};

wireEvents();
boot();

function wireEvents() {
  elements.loadButton?.addEventListener("click", () => refreshDatabase());
  elements.refreshVotesButton?.addEventListener("click", () => fetchDashboard());
  elements.districtFocusButton?.addEventListener("click", () => {
    elements.searchInput?.focus();
    elements.searchInput?.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  elements.rankingsFocusButton?.addEventListener("click", () => {
    navigateHome();
    elements.rankingSection?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  elements.searchInput?.addEventListener("input", () => {
    state.showAllRankings = false;
    syncQueryFromControls();
    renderRankings();
  });
  elements.partyFilter?.addEventListener("change", () => {
    state.showAllRankings = false;
    syncQueryFromControls();
    renderRankings();
  });
  elements.sortSelect?.addEventListener("change", () => {
    state.showAllRankings = false;
    syncQueryFromControls();
    renderRankings();
  });
  elements.showMoreButton?.addEventListener("click", () => {
    state.showAllRankings = true;
    renderRankings();
  });
  window.addEventListener("popstate", () => {
    applyQueryToControls();
    state.selectedKey = getRouteMemberKey();
    renderPageMode();
    renderRankings();
    loadSelectedDetail();
  });
}

async function boot() {
  applyQueryToControls();
  state.selectedKey = getRouteMemberKey();
  renderPageMode();

  const initialPayload = window.__INITIAL_DASHBOARD__;
  if (initialPayload?.rankings?.length) {
    await hydrateDashboard(initialPayload);
    updateStatus("", formatUpdatedMeta(initialPayload.meta));
    return;
  }

  renderLoadingState();
  showDelayedStatus(
    "랭킹 데이터를 불러오고 있습니다.",
    "저장된 결과 DB를 확인하는 중입니다.",
    250,
  );
  await fetchDashboard({ silentNotReady: true });
}

async function fetchDashboard(options = {}) {
  if (state.loading) {
    return;
  }
  state.loading = true;
  setButtonsDisabled(true);

  try {
    showDelayedStatus(
      "랭킹 데이터를 불러오고 있습니다.",
      "저장된 결과 DB에서 최신 집계 결과를 읽고 있습니다.",
      250,
    );
    const response = await fetch("/api/dashboard");
    const payload = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(payload.error || "랭킹 데이터를 읽지 못했습니다.");
    }

    await hydrateDashboard(payload);
    updateStatus("", formatUpdatedMeta(payload.meta));
  } catch (error) {
    if (options.silentNotReady) {
      renderEmptyState(
        "아직 표시할 랭킹 데이터가 없습니다.",
        "운영자 도구에서 데이터를 업데이트하거나 결과 DB를 업로드하면 시민용 랭킹이 표시됩니다.",
      );
      renderNoDataSummary();
      updateStatus("데이터 준비가 필요합니다.", "저장된 결과 DB가 아직 없습니다.");
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
    updateStatus("원본 데이터를 업데이트하고 있습니다.", "서버에서 백그라운드 동기화를 시작합니다.");
    const response = await fetch("/api/refresh", { method: "POST" });
    const payload = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(payload.error || "데이터 업데이트를 시작하지 못했습니다.");
    }
    await waitForRefreshCompletion(payload);
    await fetchDashboard();
  } catch (error) {
    handleFatalError(error);
  } finally {
    state.loading = false;
    setButtonsDisabled(false);
  }
}

async function waitForRefreshCompletion(initialStatus) {
  let statusPayload = initialStatus;
  const startedAt = Date.now();

  while (Date.now() - startedAt < 1000 * 60 * 20) {
    const status = statusPayload.status || "queued";
    if (status === "completed") {
      return;
    }
    if (status === "failed") {
      throw new Error(statusPayload.error || "데이터 업데이트 중 오류가 발생했습니다.");
    }

    updateStatus(
      statusPayload.message || "원본 데이터를 업데이트하고 있습니다.",
      statusPayload.progress_detail || "열린국회 OpenAPI에서 데이터를 수집 중입니다.",
    );
    await delay(5000);
    const response = await fetch("/api/refresh-status");
    statusPayload = await readJsonResponse(response);
  }

  throw new Error("데이터 업데이트가 시간 내에 끝나지 않았습니다. 잠시 후 다시 불러와 주세요.");
}

async function hydrateDashboard(payload) {
  state.rankings = (payload.rankings || []).map(normalizeRankingEntry);
  state.meta = payload.meta || {};
  state.detailLoadingKeys = {};
  populatePartyFilter(state.rankings);
  renderSummary(payload.summary || {});
  renderMethodology(payload.meta || {});
  renderRankings();

  if (isDetailPage() && !state.selectedKey) {
    state.selectedKey = getRouteMemberKey();
  }
  if (isDetailPage()) {
    await loadSelectedDetail();
  } else {
    state.selectedKey = null;
    renderDetails();
  }
}

async function loadSelectedDetail() {
  if (!state.selectedKey) {
    renderDetails();
    return;
  }
  await loadMemberDetail(state.selectedKey);
  renderDetails();
}

async function loadMemberDetail(memberKey) {
  if (!memberKey || state.detailLoadingKeys[memberKey]) {
    return;
  }
  const target = state.rankings.find((entry) => entry.key === memberKey);
  if (!target || (Array.isArray(target.latest_proposals) && Array.isArray(target.latest_votes))) {
    return;
  }

  state.detailLoadingKeys[memberKey] = true;
  renderDetails();
  try {
    const response = await fetch(`/api/member-detail/${encodeURIComponent(memberKey)}`);
    const payload = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(payload.error || "의원 상세 정보를 불러오지 못했습니다.");
    }
    target.latest_proposals = payload.latest_proposals || [];
    target.latest_votes = payload.latest_votes || [];
    target.news_keywords = payload.news_keywords || null;
  } catch (error) {
    console.error(error);
    target.latest_proposals = [];
    target.latest_votes = [];
    target.news_keywords = {
      available: false,
      configured: false,
      message: "뉴스 키워드를 불러오지 못했습니다.",
      months: [],
    };
  } finally {
    delete state.detailLoadingKeys[memberKey];
  }
}

function normalizeRankingEntry(entry) {
  if (!entry || entry.name !== "용혜인") {
    return entry;
  }
  return { ...entry, current_party: "기본소득당", party: "기본소득당" };
}

function renderRankings() {
  const query = elements.searchInput?.value.trim().toLowerCase() || "";
  const selectedParty = elements.partyFilter?.value || "";
  const sortKey = elements.sortSelect?.value || "score";

  const filtered = state.rankings.filter((entry) => {
    const haystack = [
      entry.name,
      entry.current_party,
      ...(entry.party_history || []),
      entry.current_district,
      ...(entry.district_history || []),
      entry.committee,
    ].filter(Boolean).join(" ").toLowerCase();
    return (!query || haystack.includes(query)) && (!selectedParty || entry.current_party === selectedParty);
  });

  const sorted = [...filtered].sort((left, right) => compareRanking(left, right, sortKey));
  const renderedRankings = state.showAllRankings ? sorted : sorted.slice(0, INITIAL_LIMIT);
  state.visibleRankings = renderedRankings;

  renderResultCount(sorted.length, renderedRankings.length);
  if (!sorted.length) {
    renderEmptyState(
      query || selectedParty ? "조건에 맞는 의원이 없습니다." : "아직 랭킹 데이터가 없습니다.",
      query || selectedParty ? "검색어 또는 정당 필터를 바꿔 다시 확인해 주세요." : "운영자 도구에서 데이터를 업데이트하면 랭킹이 표시됩니다.",
    );
    renderDetails();
    return;
  }

  elements.rankingBody.innerHTML = renderedRankings.map((entry, index) => {
    const displayRank = index + 1;
    const selectedClass = entry.key === state.selectedKey ? "is-selected" : "";
    const attendanceClass = entry.attendance_rate >= 90 ? "metric-up" : entry.attendance_rate < 70 ? "metric-warn" : "";
    return `
      <tr class="${selectedClass}" data-key="${escapeAttribute(entry.key)}">
        <td>${renderRankBadge(displayRank)}</td>
        <td>
          <div class="name-cell">
            <img class="avatar" src="${escapeAttribute(entry.photo_url || "")}" alt="${escapeAttribute(entry.name)}">
            <div>
              <strong>${escapeHtml(entry.name)}</strong>
              <span>${escapeHtml(entry.reelection || "-")}</span>
            </div>
          </div>
        </td>
        <td>${renderPartyCell(entry)}</td>
        <td class="${attendanceClass}">${formatPercent(entry.attendance_rate)}</td>
        <td>${Number(entry.proposal_count || 0).toLocaleString("ko-KR")}건</td>
        <td>${Number(entry.processed_proposal_count || 0).toLocaleString("ko-KR")}건</td>
        <td><strong>${Number(entry.score || 0).toFixed(1)}</strong></td>
      </tr>
    `;
  }).join("");

  [...elements.rankingBody.querySelectorAll("tr[data-key]")].forEach((row) => {
    row.addEventListener("click", () => navigateToMember(row.dataset.key));
  });

  if (elements.showMoreButton) {
    elements.showMoreButton.hidden = sorted.length <= INITIAL_LIMIT || state.showAllRankings;
  }
  renderDetails();
}

function renderResultCount(totalCount, renderedCount) {
  if (!elements.resultCount) {
    return;
  }
  if (!totalCount) {
    elements.resultCount.textContent = "표시할 의원 없음";
    return;
  }
  elements.resultCount.textContent = totalCount > renderedCount
    ? `전체 ${totalCount.toLocaleString("ko-KR")}명 중 ${renderedCount.toLocaleString("ko-KR")}명 표시`
    : `${totalCount.toLocaleString("ko-KR")}명`;
}

function renderLoadingState() {
  elements.rankingBody.innerHTML = `
    <tr class="skeleton-row"><td colspan="7"><span></span></td></tr>
    <tr class="skeleton-row"><td colspan="7"><span></span></td></tr>
    <tr class="skeleton-row"><td colspan="7"><span></span></td></tr>
  `;
  elements.resultCount.textContent = "랭킹을 준비 중입니다";
}

function renderEmptyState(title, description) {
  elements.rankingBody.innerHTML = `
    <tr>
      <td colspan="7" class="empty">
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(description)}</span>
      </td>
    </tr>
  `;
  elements.showMoreButton.hidden = true;
  elements.resultCount.textContent = "표시할 데이터 없음";
}

function renderSummary(summary) {
  setSummaryValue(elements.memberCount, `${Number(summary.member_count || 0).toLocaleString("ko-KR")}명`);
  setSummaryValue(elements.avgAttendance, formatPercent(summary.average_attendance_rate || 0));
  setSummaryValue(elements.totalProposals, `${Number(summary.total_proposals || 0).toLocaleString("ko-KR")}건`);
  setSummaryValue(
    elements.topProposer,
    summary.top_proposer_name
      ? `${summary.top_proposer_name} (${Number(summary.top_proposal_count || 0).toLocaleString("ko-KR")}건)`
      : "-",
  );
}

function renderNoDataSummary() {
  setSummaryValue(elements.memberCount, "데이터 없음");
  setSummaryValue(elements.avgAttendance, "-");
  setSummaryValue(elements.totalProposals, "-");
  setSummaryValue(elements.topProposer, "-");
}

function setSummaryValue(element, value) {
  if (!element) {
    return;
  }
  element.classList.remove("skeleton-text");
  element.textContent = value;
}

function renderMethodology(meta) {
  if (elements.assemblyLabel) {
    elements.assemblyLabel.textContent = meta.assembly_label || "제22대";
  }
  if (elements.lastUpdatedText) {
    elements.lastUpdatedText.textContent = formatUpdatedTime(meta.last_synced_at);
  }
}

function renderDetails() {
  const selected = state.rankings.find((entry) => entry.key === state.selectedKey);
  if (!selected) {
    elements.detailCard.innerHTML = `
      <div class="detail-card__placeholder">
        ${isDetailPage() ? "의원 정보를 찾을 수 없습니다." : "의원을 선택하면 전용 상세 페이지로 이동합니다."}
      </div>
    `;
    return;
  }

  const isLoadingDetail = state.detailLoadingKeys[selected.key];
  const proposals = renderProposalHistory(selected, isLoadingDetail);
  const votes = renderVoteHistory(selected, isLoadingDetail);
  const newsKeywords = renderNewsKeywordCloud(selected.news_keywords);
  const shareUrl = `${window.location.origin}/member/${encodeURIComponent(selected.key)}`;

  elements.detailCard.innerHTML = `
    <div class="detail-card__header">
      <img class="avatar avatar--large" src="${escapeAttribute(selected.photo_url || "")}" alt="${escapeAttribute(selected.name)}">
      <div>
        <p class="eyebrow">${isDetailPage() ? "Lawmaker Detail" : "선택한 의원"}</p>
        <h2 class="detail-card__title">${escapeHtml(selected.name)}</h2>
        <div class="detail-card__sub">${renderPartyVisual(selected.current_party, "large")} · ${escapeHtml(selected.current_district || "지역구 정보 없음")}</div>
        ${renderPartyHistory(selected)}
        ${renderDistrictHistory(selected)}
      </div>
    </div>

    <div class="detail-actions">
      ${isDetailPage() ? `<button class="secondary-button" type="button" data-action="back-to-ranking">랭킹으로 돌아가기</button>` : `<button class="secondary-button" type="button" data-action="open-detail">상세 페이지 열기</button>`}
      <button class="secondary-button" type="button" data-action="copy-link">공유 링크 복사</button>
      <span class="detail-actions__hint">마지막 업데이트: ${escapeHtml(formatUpdatedTime(state.meta.last_synced_at))}</span>
    </div>

    <div class="detail-card__grid">
      ${renderMetric("종합점수", `${Number(selected.score || 0).toFixed(1)}점`)}
      ${renderMetric("출석률", formatPercent(selected.attendance_rate))}
      ${renderMetric("대표발의", `${Number(selected.proposal_count || 0).toLocaleString("ko-KR")}건`)}
      ${renderMetric("처리의안", `${Number(selected.processed_proposal_count || 0).toLocaleString("ko-KR")}건`)}
    </div>

    <div class="score-note">
      종합점수는 출석률 65%, 대표발의 25%, 처리의안 10%를 반영한 참고 지표입니다.
    </div>

    <div class="detail-section">
      <h3>기본 정보</h3>
      <div class="detail-list">
        ${renderDetailItem("소속 위원회", selected.committee || "-")}
        <div class="detail-item">
          <strong>연락처</strong>
          <div class="contact-links">
            ${renderContactLink("phone", selected.phone)}
            ${renderContactLink("email", selected.email)}
          </div>
        </div>
        <div class="detail-item">
          <strong>홈페이지</strong>
          ${selected.homepage_url ? `<a href="${escapeAttribute(selected.homepage_url)}" target="_blank" rel="noreferrer">${escapeHtml(selected.homepage_url)}</a>` : `<span>-</span>`}
        </div>
      </div>
    </div>

    <div class="detail-section">
      <h3>뉴스 키워드</h3>
      <div class="detail-list">${newsKeywords}</div>
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

  elements.detailCard.querySelector('[data-action="open-detail"]')?.addEventListener("click", () => navigateToMember(selected.key));
  elements.detailCard.querySelector('[data-action="back-to-ranking"]')?.addEventListener("click", () => navigateHome());
  elements.detailCard.querySelector('[data-action="copy-link"]')?.addEventListener("click", async () => {
    await navigator.clipboard?.writeText(shareUrl);
    updateStatus("공유 링크를 복사했습니다.", shareUrl);
  });
}

function renderMetric(label, value) {
  return `
    <div class="detail-metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

function renderDetailItem(label, value) {
  return `
    <div class="detail-item">
      <strong>${escapeHtml(label)}</strong>
      <span>${escapeHtml(value)}</span>
    </div>
  `;
}

function renderProposalHistory(selected, isLoading) {
  if (isLoading || !Array.isArray(selected.latest_proposals)) {
    return `<div class="detail-item"><span>상세 이력을 불러오는 중입니다.</span></div>`;
  }
  if (!selected.latest_proposals.length) {
    return `<div class="detail-item"><span>저장된 대표발의 이력이 없습니다.</span></div>`;
  }
  return selected.latest_proposals.slice(0, 2).map((item) => `
    <div class="detail-item">
      <strong>${item.link_url ? `<a href="${escapeAttribute(item.link_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.bill_name || "-")}</a>` : escapeHtml(item.bill_name || "-")}</strong>
      <span>의안번호 ${escapeHtml(item.bill_no || "-")} · 제안일 ${escapeHtml(item.proposed_date || "-")} · 상태: ${escapeHtml(item.result || "-")}</span>
    </div>
  `).join("");
}

function renderVoteHistory(selected, isLoading) {
  if (isLoading || !Array.isArray(selected.latest_votes)) {
    return `<div class="detail-item"><span>상세 이력을 불러오는 중입니다.</span></div>`;
  }
  if (!selected.latest_votes.length) {
    return `<div class="detail-item"><span>저장된 표결 이력이 없습니다.</span></div>`;
  }
  return selected.latest_votes.slice(0, 2).map((item) => `
    <div class="detail-item">
      <strong>${item.link_url ? `<a href="${escapeAttribute(item.link_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.bill_name || "-")}</a>` : escapeHtml(item.bill_name || "-")}</strong>
      <span>${escapeHtml(formatVoteDate(item.vote_date))} · ${escapeHtml(item.result_vote_mod || "-")}</span>
    </div>
  `).join("");
}

function renderNewsKeywordCloud(newsKeywords) {
  if (!newsKeywords) {
    return `<div class="detail-item"><span>뉴스 키워드를 불러오는 중입니다.</span></div>`;
  }
  if (!newsKeywords.available || !Array.isArray(newsKeywords.months) || !newsKeywords.months.length) {
    return `<div class="detail-item"><span>${escapeHtml(newsKeywords.message || "뉴스 키워드가 없습니다.")}</span></div>`;
  }

  return newsKeywords.months.slice(0, 1).map((monthEntry) => {
    const keywordItems = monthEntry.keywords || [];
    const imageSrc = createWordCloudImage(keywordItems);
    const articleCount = Number(monthEntry.article_count || 0).toLocaleString("ko-KR");
    return `
      <div class="detail-item detail-item--stacked detail-item--cloud">
        <strong>${escapeHtml(monthEntry.month)}(뉴스 ${articleCount}건)</strong>
        <div class="keyword-cloud-image-wrap">
          ${keywordItems.length
            ? `<img class="keyword-cloud-image" src="${imageSrc}" alt="${escapeAttribute(monthEntry.month)} 뉴스 키워드 워드클라우드">`
            : `<span class="keyword-cloud__word keyword-cloud__word--muted">키워드 없음</span>`}
        </div>
      </div>
    `;
  }).join("");
}

function createWordCloudImage(keywords) {
  const width = 760;
  const height = 420;
  const palette = ["#39c58a", "#5d9cec", "#b18af2", "#f06292", "#f6b23c", "#51c7d9", "#7bc96f"];
  const layout = [
    { x: 0.22, y: 0.2 }, { x: 0.7, y: 0.18 }, { x: 0.4, y: 0.4 }, { x: 0.74, y: 0.44 },
    { x: 0.18, y: 0.54 }, { x: 0.58, y: 0.62 }, { x: 0.32, y: 0.74 }, { x: 0.82, y: 0.7 },
    { x: 0.1, y: 0.3 }, { x: 0.52, y: 0.22 }, { x: 0.86, y: 0.28 }, { x: 0.12, y: 0.76 },
    { x: 0.44, y: 0.84 }, { x: 0.66, y: 0.82 }, { x: 0.9, y: 0.56 }, { x: 0.06, y: 0.6 },
    { x: 0.34, y: 0.12 }, { x: 0.56, y: 0.48 }, { x: 0.78, y: 0.12 }, { x: 0.24, y: 0.9 },
  ];
  const maxCount = Math.max(...keywords.map((item) => Number(item.count || 0)), 1);
  const words = keywords.slice(0, 20).map((item, index) => {
    const weight = Number(item.count || 0) / maxCount;
    const fontSize = weight >= 0.9 ? 112 : weight >= 0.7 ? 84 : weight >= 0.5 ? 64 : weight >= 0.3 ? 44 : 32;
    const slot = layout[index % layout.length];
    const x = Math.round(slot.x * width);
    const y = Math.round(slot.y * height);
    const rotate = [-8, 6, -4, 4, -6, 0][index % 6];
    return `<text x="${x}" y="${y}" text-anchor="middle" dominant-baseline="middle" fill="${palette[index % palette.length]}" font-size="${fontSize}" font-weight="700" transform="rotate(${rotate} ${x} ${y})">${escapeHtml(item.keyword)}</text>`;
  }).join("");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><rect width="100%" height="100%" fill="#ffffff"/>${words}</svg>`;
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function populatePartyFilter(rankings) {
  const currentValue = elements.partyFilter?.value || "";
  const parties = [...new Set(rankings.map((item) => item.current_party).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ko"));
  elements.partyFilter.innerHTML = `<option value="">전체 정당</option>${parties.map((party) => `<option value="${escapeAttribute(party)}">${escapeHtml(party)}</option>`).join("")}`;
  if (parties.includes(currentValue)) {
    elements.partyFilter.value = currentValue;
  }
}

function renderPartyCell(entry) {
  return `<div class="party-cell">${renderPartyVisual(entry.current_party || entry.party || "-", "small")}</div>`;
}

function renderPartyHistory(entry) {
  const history = entry.party_history || [];
  if (!history.length) {
    return "";
  }
  return `
    <details class="party-history party-history--detail">
      <summary>이전 정당 보기</summary>
      <div class="party-history__list">${history.map(escapeHtml).join(" · ")}</div>
    </details>
  `;
}

function renderDistrictHistory(entry) {
  const history = entry.district_history || [];
  if (!history.length) {
    return "";
  }
  return `
    <details class="party-history party-history--detail">
      <summary>이전 지역구 보기</summary>
      <div class="party-history__list">${history.map(escapeHtml).join(" · ")}</div>
    </details>
  `;
}

function renderPartyVisual(partyName, size = "small") {
  const label = partyName || "-";
  const src = createPartyLogoDataUri(label);
  const className = size === "large" ? "party-logo party-logo--large" : "party-logo";
  return `<img class="${className}" src="${src}" alt="${escapeAttribute(label)} 로고">`;
}

function createPartyLogoDataUri(partyName) {
  const style = PARTY_LOGO_STYLES[partyName] || { bg: "#5e6d81", fg: "#ffffff", border: "#304863" };
  const safeText = escapeHtml(partyName);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="220" height="52" viewBox="0 0 220 52"><rect x="1" y="1" width="218" height="50" rx="16" fill="${style.bg}" stroke="${style.border}" stroke-width="2"/><text x="110" y="33" text-anchor="middle" font-size="24" font-family="IBM Plex Sans KR, sans-serif" font-weight="700" fill="${style.fg}">${safeText}</text></svg>`;
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function renderContactLink(type, value) {
  const text = String(value || "").trim();
  const icon = renderInlineIcon(type);
  if (!text) {
    return `<span class="contact-link contact-link--muted">${icon}<span>-</span></span>`;
  }
  if (type === "phone") {
    return `<a class="contact-link" href="tel:${escapeAttribute(text.replace(/[^+\d]/g, ""))}">${icon}<span>${escapeHtml(text)}</span></a>`;
  }
  return `<a class="contact-link" href="mailto:${escapeAttribute(text)}">${icon}<span>${escapeHtml(text)}</span></a>`;
}

function renderInlineIcon(type) {
  if (type === "phone") {
    return `<svg class="contact-link__icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M6.6 10.8a15.6 15.6 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.24 11.2 11.2 0 0 0 3.5.56 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1C10.6 21 3 13.4 3 4a1 1 0 0 1 1-1h3.3a1 1 0 0 1 1 1 11.2 11.2 0 0 0 .56 3.5 1 1 0 0 1-.24 1z"/></svg>`;
  }
  return `<svg class="contact-link__icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zm0 2v.2l8 5.33 8-5.33V7H4zm16 10V9.6l-7.45 4.96a1 1 0 0 1-1.1 0L4 9.6V17h16z"/></svg>`;
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
  };
  const mapped = valueMap[sortKey] || "score";
  return (right[mapped] ?? 0) - (left[mapped] ?? 0) || right.score - left.score;
}

function navigateToMember(memberKey) {
  if (!memberKey) {
    return;
  }
  state.selectedKey = memberKey;
  const query = buildQueryString();
  window.history.pushState({}, "", `/member/${encodeURIComponent(memberKey)}${query}`);
  renderPageMode();
  renderRankings();
  loadSelectedDetail();
}

function navigateHome() {
  const query = buildQueryString();
  window.history.pushState({}, "", `/${query}`);
  state.selectedKey = null;
  renderPageMode();
  renderRankings();
  renderDetails();
}

function isDetailPage() {
  return Boolean(getRouteMemberKey());
}

function getRouteMemberKey() {
  const match = window.location.pathname.match(/^\/member\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function renderPageMode() {
  document.body.classList.toggle("is-detail-page", isDetailPage());
}

function applyQueryToControls() {
  const params = new URLSearchParams(window.location.search);
  if (elements.searchInput) {
    elements.searchInput.value = params.get("q") || "";
  }
  if (elements.partyFilter) {
    elements.partyFilter.value = params.get("party") || "";
  }
  if (elements.sortSelect) {
    elements.sortSelect.value = params.get("sort") || "score";
  }
}

function syncQueryFromControls() {
  const query = buildQueryString();
  const path = window.location.pathname;
  window.history.replaceState({}, "", `${path}${query}`);
}

function buildQueryString() {
  const params = new URLSearchParams();
  const q = elements.searchInput?.value.trim();
  const party = elements.partyFilter?.value;
  const sort = elements.sortSelect?.value;
  if (q) params.set("q", q);
  if (party) params.set("party", party);
  if (sort && sort !== "score") params.set("sort", sort);
  const text = params.toString();
  return text ? `?${text}` : "";
}

function readJsonResponse(response) {
  return response.text().then((text) => {
    try {
      return text ? JSON.parse(text) : {};
    } catch {
      throw new Error(text || "서버가 JSON이 아닌 응답을 반환했습니다.");
    }
  });
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function updateStatus(message, meta) {
  if (state.statusDelayTimer) {
    window.clearTimeout(state.statusDelayTimer);
    state.statusDelayTimer = null;
  }
  elements.statusMessage.textContent = message || "";
  elements.statusMeta.textContent = meta || "";
}

function showDelayedStatus(message, meta, delay = 250) {
  if (state.statusDelayTimer) {
    window.clearTimeout(state.statusDelayTimer);
  }
  state.statusDelayTimer = window.setTimeout(() => updateStatus(message, meta), delay);
}

function handleFatalError(error) {
  console.error(error);
  updateStatus("문제가 발생했습니다.", error.message || "알 수 없는 오류입니다.");
  renderEmptyState("데이터를 불러오지 못했습니다.", error.message || "잠시 후 다시 시도해 주세요.");
  elements.detailCard.innerHTML = `<div class="detail-card__placeholder">오류가 해결되면 다시 시도해 주세요.</div>`;
}

function setButtonsDisabled(disabled) {
  elements.loadButton.disabled = disabled;
  elements.refreshVotesButton.disabled = disabled;
}

function formatPercent(value) {
  return `${Number(value || 0).toFixed(1)}%`;
}

function formatVoteDate(value) {
  const text = String(value || "").replace(/\s+/g, "");
  if (text.length < 8) return value || "-";
  return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
}

function formatUpdatedTime(value) {
  if (!value) {
    return "업데이트 이력 없음";
  }
  return value;
}

function formatUpdatedMeta(meta = {}) {
  return `데이터 동기화 시간: ${formatUpdatedTime(meta.last_synced_at)}`;
}

function renderRankBadge(rank) {
  const medalMap = {
    1: { symbol: "🥇", className: "rank-badge rank-badge--gold", label: "1위" },
    2: { symbol: "🥈", className: "rank-badge rank-badge--silver", label: "2위" },
    3: { symbol: "🥉", className: "rank-badge rank-badge--bronze", label: "3위" },
  };
  const medal = medalMap[Number(rank)];
  if (medal) {
    return `<span class="${medal.className}" aria-label="${medal.label}" title="${medal.label}">${medal.symbol}</span>`;
  }
  return `<span class="rank-badge">${Number(rank || 0)}</span>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replace(/'/g, "&#39;");
}
