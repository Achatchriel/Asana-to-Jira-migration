// Copyright (c) 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.
// Private, non-commercial use only. Any commercial use is strictly prohibited. See LICENSE.
/*
 * assign_statuses_to_columns.js
 *
 * WKLEJ TO W KONSOLI PRZEGLĄDARKI (F12 -> Console), będąc zalogowanym do
 * Jiry, na DOWOLNEJ stronie w obrębie twoja-instancja.atlassian.net.
 *
 * Odkryte przez podgląd Network podczas ręcznego przeciągania statusu:
 *   - ODCZYT: GraphQL, persisted query "ColumnsSettingsPageNewContentQuery"
 *   - ZAPIS:  PUT /rest/greenhopper/1.0/rapidviewconfig/columns (stare,
 *             nieudokumentowane API "GreenHopper" - poprzednik dzisiejszej
 *             nazwy "Jira Software"; działa, bo tego samego endpointu
 *             używa wewnętrznie UI Jiry, ale Atlassian NIE gwarantuje, że
 *             pozostanie stabilny - może się zmienić bez ostrzeżenia).
 *
 * UWAGA: to NIEOFICJALNE API. Testuj najpierw na JEDNYM projekcie testowym.
 *
 * Użycie (w konsoli, po wklejeniu całego pliku):
 *
 *   await assignStatusesToColumns(7932, {
 *     "New In": ["New", "Postponed"],
 *     "To Be Defined": ["Selected"],
 *     "To Be Accepted": ["Approval Required"],
 *     "To Do": ["Ready"],
 *     "In Progress": ["In Progress", "Customer Testing"],
 *     "Interne qs": ["Internal Testing"],
 *     "Doku": ["Documentation"],
 *     "Review kundle": ["Customer Testing"],
 *     "Done": ["Fertig"],
 *     "Blocked": ["Feedback Required"],
 *   });
 *
 * Klucze obiektu = DOKŁADNE nazwy kolumn na tablicy (takie jak w Jirze).
 * Wartości = lista DOKŁADNYCH nazw statusów, które mają tam trafić.
 * Status nie wymieniony w żadnej kolumnie zostaje nietknięty (tam gdzie był
 * - w innej kolumnie albo w "Unmapped statuses").
 */

// --- KONFIGURACJA (stała dla całej instancji twoja-instancja.atlassian.net) -----
const CLOUD_ID = "ec8e758d-8ba8-45df-a7cb-5aaf86e05b13";
const WORKSPACE_ID = "8af535a1-ddb9-4924-8e73-43bc3e4e886b";
const GRAPHQL_QUERY_HASH = "ee2eca73f267aac1af28737f7ac9a48bb0c4aa424206b5946ceb79d2a4cfeb47";
const GRAPHQL_OPERATION = "ColumnsSettingsPageNewContentQuery";
// Ta lista + atl-client-version są specyficzne dla konkretnego builda frontendu
// Jiry - jeśli po jakiejś aktualizacji Jiry skrypt przestanie działać (błąd
// 400/415 przy GraphQL), najpewniej trzeba będzie podmienić te dwie stałe,
// świeżo przechwycone z Network (Request Headers -> x-experimentalapi,
// atl-client-version).
const X_EXPERIMENTAL_API = "ariGraph,boardCardMove,deleteCard,JiraJqlBuilder,SoftwareCardTypeTransitions,jira-releases-v0,JiraDevOps,JiraDevOpsProviders,createCustomFilter,updateCustomFilter,deleteCustomFilter,customFilters,PermissionScheme,JiraIssue,projectStyle,startSprintPrototype,AddIssuesToFixVersion,JiraVersionResult,JiraIssueConnectionJql,JiraFieldOptionSearching,JiraIssueFieldMutations,JiraIssueDevInfoDetails,JiraIssueDevSummaryCache,JiraVersionWarningConfig,JiraReleaseNotesConfiguration,JiraUpdateReleaseNotesConfiguration,ReleaseNotes,ReleaseNotesOptions,DeploymentsFeaturePrecondition,UpdateVersionWarningConfig,UpdateVersionName,UpdateVersionDescription,UpdateVersionStartDate,UpdateVersionReleaseDate,VersionsForProject,RelatedWork,SuggestedRelatedWorkCategories,setIssueMediaVisibility,toggleBoardFeature,DevOpsProvider,DevOpsSummarisedDeployments,JiraProject,DevOpsSummarisedEntities,RoadmapsMutation,RoadmapsQuery,JiraApplicationProperties,JiraIssueSearch,JiraFilter,featureGroups,setBoardEstimationType,devOps,softwareBoards,name,AddRelatedWorkToVersion,RemoveRelatedWorkFromVersion,admins,canAdministerBoard,jql,globalCardCreateAdditionalFields,GlobalTimeTrackingSettings,ReleaseNotesOptions,search-experience,MoveOrRemoveIssuesToFixVersion,compass-beta,JiraIssueSearchStatus,Townsquare,JiraNavigationItems,JiraVersionsForProjects,columnConfigs,help-object-store-agg-beta,HelpCenterReorderTopics,polaris-v0";
const ATL_CLIENT_VERSION = "1.417010.0-68ad4805671865::1.417010.0-68ad4805671865";

// Stałe, znane wartości kategorii statusu w Jirze (te same dla każdej
// instancji Jira Cloud - potwierdzone w przechwyconych danych).
const CATEGORY_MAP = {
  new: { id: "2", colorName: "blue-gray" },
  indeterminate: { id: "4", colorName: "yellow" },
  done: { id: "3", colorName: "green" },
};

function getCookie(name) {
  const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : null;
}

async function fetchCurrentColumnConfig(boardId) {
  const ari = `ari:cloud:jira-software:${CLOUD_ID}:board/${boardId}`;
  const variables = encodeURIComponent(JSON.stringify({ boardId: ari, cloudId: CLOUD_ID }));
  const url = `/gateway/api/graphql/pq/${GRAPHQL_QUERY_HASH}?operation=${GRAPHQL_OPERATION}&variables=${variables}`;

  const xsrfToken = getCookie("atlassian.xsrf.token");

  const resp = await fetch(url, {
    headers: {
      Accept: "application/graphql-response+json, application/json, multipart/mixed;deferSpec=20220824",
      "Content-Type": "application/json",
      "atl-attribution": JSON.stringify({
        atlWorkspaceId: `ari:cloud:jira:${CLOUD_ID}:workspace/${WORKSPACE_ID}`,
        tenantId: CLOUD_ID,
        product: "Atlassian Jira",
        service: "afm-frontend",
      }),
      "atl-client-name": "atlassian-frontend-monorepo",
      "atl-client-version": ATL_CLIENT_VERSION,
      "atl-xsrf-token": xsrfToken,
      "x-experimentalapi": X_EXPERIMENTAL_API,
      "x-query-context": `ari:cloud:platform::site/${CLOUD_ID}`,
    },
  });
  if (!resp.ok) {
    throw new Error(`Nie udało się pobrać konfiguracji (GraphQL): ${resp.status} ${await resp.text()}`);
  }
  const json = await resp.json();
  const config = json?.data?.boardScope?.columnsConfig;
  if (!config) {
    throw new Error("Nieoczekiwany kształt odpowiedzi GraphQL - sprawdź ręcznie w Network, czy struktura się nie zmieniła.");
  }
  return config;
}

function statusToMappedStatus(status) {
  const cat = CATEGORY_MAP[status.category] || { id: "1", colorName: "gray" };
  return {
    id: status.id,
    name: status.name,
    iconUrl: "https://twoja-instancja.atlassian.net/images/icons/statuses/generic.png",
    statusCategory: { id: cat.id, key: status.category, colorName: cat.colorName },
    issueCount: status.issueMetaData?.issueCount ?? 0,
    isPresentInWorkflow: status.isPresentInWorkflow ?? true,
  };
}

async function assignStatusesToColumns(boardId, targetMapping) {
  console.log(`[1/4] Pobieram bieżącą konfigurację tablicy ${boardId}...`);
  const config = await fetchCurrentColumnConfig(boardId);

  // Zbierz WSZYSTKIE statusy (z kolumn + unmapped) w jedną, płaską mapę po ID,
  // żeby móc je łatwo przenosić bez utraty danych (issueCount, category itd.).
  const allStatusesById = new Map();
  for (const col of config.columns) {
    for (const cs of col.columnStatus) {
      allStatusesById.set(cs.status.id, cs.status);
    }
  }
  for (const s of config.unmappedStatuses) {
    allStatusesById.set(s.id, s);
  }
  console.log(`    Znaleziono ${allStatusesById.size} statusów łącznie (zmapowanych + niezmapowanych).`);

  const nameToId = new Map();
  for (const s of allStatusesById.values()) {
    nameToId.set(s.name.trim().toLowerCase(), s.id);
  }

  const assignedStatusIds = new Set();
  const newMappedColumns = config.columns.map((col) => {
    const wantedNames = targetMapping[col.name] || targetMapping[col.name.trim()];
    if (!wantedNames) {
      const keepStatuses = col.columnStatus.map((cs) => cs.status.id);
      keepStatuses.forEach((id) => assignedStatusIds.add(id));
      return {
        id: col.id,
        name: col.name,
        min: col.minCardCount ?? "",
        max: col.maxCardCount ?? "",
        mappedStatuses: col.columnStatus.map((cs) => statusToMappedStatus(cs.status)),
        isKanPlanColumn: col.isKanPlanColumn,
        issueCount: 0,
      };
    }

    const statusesForThisColumn = [];
    for (const wantedName of wantedNames) {
      const id = nameToId.get(wantedName.trim().toLowerCase());
      if (!id) {
        console.warn(`    [OSTRZEŻENIE] Status '${wantedName}' (dla kolumny '${col.name}') nie istnieje w tym projekcie - pomijam.`);
        continue;
      }
      statusesForThisColumn.push(statusToMappedStatus(allStatusesById.get(id)));
      assignedStatusIds.add(id);
    }

    return {
      id: col.id,
      name: col.name,
      min: col.minCardCount ?? "",
      max: col.maxCardCount ?? "",
      mappedStatuses: statusesForThisColumn,
      isKanPlanColumn: col.isKanPlanColumn,
      issueCount: 0,
    };
  });

  const newUnmapped = [...allStatusesById.values()]
    .filter((s) => !assignedStatusIds.has(s.id))
    .map(statusToMappedStatus);

  const payload = {
    rapidViewId: Number(boardId),
    canEdit: true,
    currentStatisticsField: {
      typeId: "issueCount",
      fieldId: "",
      id: config.constraintsStatisticsField.currentId,
      name: "Issue count",
      isValid: true,
      isEnabled: true,
      renderer: "number",
    },
    showDaysInColumn: false,
    statisticsFieldValidation: { isValid: true },
    statisticsFields: config.constraintsStatisticsField.availableConstraints.map((c) => ({
      typeId: c.id.replace(/_$/, ""),
      id: c.id,
      name: c.name,
      isValid: true,
      isEnabled: c.id === config.constraintsStatisticsField.currentId,
      renderer: "number",
    })),
    mappedColumns: newMappedColumns,
    unmappedStatuses: newUnmapped,
    workflow: {
      isSimplifiedWorkflowFeatureEnabled: true,
      usingSimplifiedWorkflow: config.workflow.usingSimplifiedWorkflow,
      canSimplifyWorkflow: config.workflow.canSimplifyWorkflow,
      userCanSimplifyWorkflow: config.workflow.userCanSimplifyWorkflow,
    },
  };

  console.log("[2/4] Zbudowano nowy układ. Podgląd (kolumna: liczba statusów):");
  newMappedColumns.forEach((c) => console.log(`    ${c.name}: ${c.mappedStatuses.length}`));
  console.log(`    Unmapped: ${newUnmapped.length}`);

  console.log("[3/4] Wysyłam PUT...");
  const putResp = await fetch("/rest/greenhopper/1.0/rapidviewconfig/columns", {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });

  console.log(`[4/4] Odpowiedź: ${putResp.status}`);
  if (!putResp.ok) {
    console.error(await putResp.text());
    throw new Error(`PUT nie powiódł się: ${putResp.status}`);
  }
  console.log("OK - sprawdź tablicę (odśwież stronę), czy wygląda poprawnie.");
  return await putResp.json();
}

console.log("Gotowe. Wywołaj: await assignStatusesToColumns(BOARD_ID, { \"Nazwa kolumny\": [\"Status1\", \"Status2\"], ... })");

/*
 * Przetwarza WIELE tablic jednym przebiegiem. boardConfigs to lista obiektów:
 *   [{ boardId: 7932, projectKey: "MT", mapping: { "New In": ["New"], ... } }, ...]
 * (projectKey jest opcjonalny - tylko do czytelniejszych logów).
 *
 * Użycie:
 *   const configs = [ ... ];  // wklej wygenerowaną listę z generate_column_assignments.py
 *   await assignStatusesToManyBoards(configs);
 */
async function assignStatusesToManyBoards(boardConfigs, delayMs = 800) {
  const report = [];
  for (let i = 0; i < boardConfigs.length; i++) {
    const { boardId, projectKey, mapping } = boardConfigs[i];
    const label = projectKey ? `${projectKey} (board ${boardId})` : `board ${boardId}`;
    console.log(`\n[${i + 1}/${boardConfigs.length}] ${label}`);
    try {
      await assignStatusesToColumns(boardId, mapping);
      report.push({ boardId, projectKey, status: "ok" });
    } catch (err) {
      console.error(`  BŁĄD: ${err.message}`);
      report.push({ boardId, projectKey, status: "blad", error: String(err.message) });
    }
    if (i < boardConfigs.length - 1) {
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }

  const ok = report.filter((r) => r.status === "ok").length;
  const failed = report.filter((r) => r.status === "blad");
  console.log("\n" + "=".repeat(60));
  console.log("PODSUMOWANIE");
  console.log(`  OK:    ${ok}`);
  console.log(`  Błędy: ${failed.length}`);
  if (failed.length) {
    console.log("  Projekty z błędem:");
    failed.forEach((f) => console.log(`    - ${f.projectKey || f.boardId}: ${f.error}`));
  }
  console.log("\nPełny raport w zmiennej (skopiuj do pliku, jeśli chcesz zachować):");
  console.log(JSON.stringify(report, null, 2));
  return report;
}
