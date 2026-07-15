-- ============================================================
-- NGP2 Sync Preview  |  db_ProcessData → ngp2_may7n-27n
-- ============================================================
-- Run this BEFORE ngp2_sync.sql to check what would be inserted.
-- Connect to the LOCAL SQL Server instance in DBeaver (ngp2_may7n-27n host).
-- Requires the [ProcessData_Src] linked server to be set up first:
--   run ngp2_linked_server_setup.sql once (needs sysadmin).
-- Output: one row per table showing local max date and pending row count.
-- ============================================================

USE [ngp2_may7n-27n];

-- ── Update this each week ──────────────────────────────────────────────────
DECLARE @cutoff DATETIME = '2026-06-20 07:07:00';
-- ──────────────────────────────────────────────────────────────────────────
-- 4194707

-- Build local max timestamps into a temp table, then join with source counts
-- in one result set so you can read the whole picture at a glance.

CREATE TABLE #preview (
    grp         NVARCHAR(20),
    table_name  NVARCHAR(200),
    ts_col      NVARCHAR(50),
    local_max   DATETIME,
    pending     INT
);

-- ── Core ──────────────────────────────────────────────────────────────────────
DECLARE @m DATETIME;

SELECT @m = MAX(Start_TS) FROM dbo.tbl_SCP_NGP2_StatusBlocks WHERE Start_TS < @cutoff;
INSERT INTO #preview SELECT 'core', 'tbl_SCP_NGP2_StatusBlocks', 'Start_TS', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_SCP_NGP2_StatusBlocks
     WHERE Start_TS > @m AND Start_TS < @cutoff);

SELECT @m = MAX(Start_TS) FROM dbo.tbl_SCP_NGP2_DowntimeOverlays WHERE Start_TS < @cutoff;
INSERT INTO #preview SELECT 'core', 'tbl_SCP_NGP2_DowntimeOverlays', 'Start_TS', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_SCP_NGP2_DowntimeOverlays
     WHERE Start_TS > @m AND Start_TS < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_FOFchanges_WithReasonCode;
INSERT INTO #preview SELECT 'core', 'tbl_TG_NGP2_HH_FOFchanges_WithReasonCode', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_FOFchanges_WithReasonCode
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_FOFchanges_WithReasonCode_ALT;
INSERT INTO #preview SELECT 'core', 'tbl_TG_NGP2_HH_FOFchanges_WithReasonCode_ALT', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_FOFchanges_WithReasonCode_ALT
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_LiveOEE;
INSERT INTO #preview SELECT 'core', 'tbl_TG_NGP2_HH_LiveOEE', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_LiveOEE
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_OmacState;
INSERT INTO #preview SELECT 'core', 'tbl_TG_NGP2_HH_OmacState', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_OmacState
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_OutputStats;
INSERT INTO #preview SELECT 'core', 'tbl_TG_NGP2_HH_OutputStats', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_OutputStats
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_PartsPerMinute;
INSERT INTO #preview SELECT 'core', 'tbl_TG_NGP2_HH_PartsPerMinute', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_PartsPerMinute
     WHERE t_stamp > @m AND t_stamp < @cutoff);

-- ── Hydration ─────────────────────────────────────────────────────────────────
SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_AlarmStack;
INSERT INTO #preview SELECT 'hydration', 'tbl_TG_NGP2_HH_AlarmStack', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_AlarmStack
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_Buttons_TEMP;
INSERT INTO #preview SELECT 'hydration', 'tbl_TG_NGP2_HH_Buttons_TEMP', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_Buttons_TEMP
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_OperatorStopEvents;
INSERT INTO #preview SELECT 'hydration', 'tbl_TG_NGP2_HH_OperatorStopEvents', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_OperatorStopEvents
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_Resets_TEMP;
INSERT INTO #preview SELECT 'hydration', 'tbl_TG_NGP2_HH_Resets_TEMP', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_Resets_TEMP
     WHERE t_stamp > @m AND t_stamp < @cutoff);

-- StationRejectCounterSummary is XML — COUNT(*) also fails over linked server; use OPENQUERY.
SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_StationRejectCountSummary;
DECLARE @qp   CHAR(1)       = CHAR(39);
DECLARE @ms   VARCHAR(30)   = ISNULL(CONVERT(VARCHAR(30), @m, 121), '1900-01-01');
DECLARE @csp  VARCHAR(30)   = CONVERT(VARCHAR(30), @cutoff, 120);
DECLARE @psql NVARCHAR(MAX) =
    N'SELECT COUNT(*) FROM OPENQUERY([db_ProcessData], ' + @qp +
    N'SELECT t_stamp FROM db_ProcessData.dbo.tbl_TG_NGP2_HH_StationRejectCountSummary' +
    N' WHERE t_stamp > ' + @qp+@qp + @ms + @qp+@qp +
    N' AND t_stamp < '   + @qp+@qp + @csp + @qp+@qp + @qp + N')';
DECLARE @pending INT;
CREATE TABLE #xml_cnt (n INT);
INSERT INTO #xml_cnt EXEC sp_executesql @psql;
SELECT @pending = n FROM #xml_cnt; DROP TABLE #xml_cnt;
INSERT INTO #preview SELECT 'hydration', 'tbl_TG_NGP2_HH_StationRejectCountSummary', 't_stamp', @m, @pending;

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Format;
INSERT INTO #preview SELECT 'hydration', 'tbl_TG_NGP2_Format', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Format
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_StationsCounters_Hydration;
INSERT INTO #preview SELECT 'hydration', 'tbl_TG_NGP2_HH_StationsCounters_Hydration', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_StationsCounters_Hydration
     WHERE t_stamp > @m AND t_stamp < @cutoff);

-- ── Foil ──────────────────────────────────────────────────────────────────────
SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_HH_StationsCounters_Foil;
INSERT INTO #preview SELECT 'foil', 'tbl_TG_NGP2_HH_StationsCounters_Foil', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_HH_StationsCounters_Foil
     WHERE t_stamp > @m AND t_stamp < @cutoff);

-- ── Foam ──────────────────────────────────────────────────────────────────────
SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_CurrentProduct;
INSERT INTO #preview SELECT 'foam', 'tbl_TG_NGP2_Foam_CurrentProduct', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_CurrentProduct
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_FoamFunctionState;
INSERT INTO #preview SELECT 'foam', 'tbl_TG_NGP2_Foam_FoamFunctionState', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_FoamFunctionState
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_MachineState;
INSERT INTO #preview SELECT 'foam', 'tbl_TG_NGP2_Foam_MachineState', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_MachineState
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_MessageTextRaw;
INSERT INTO #preview SELECT 'foam', 'tbl_TG_NGP2_Foam_MessageTextRaw', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_MessageTextRaw
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_AirInjectFlowRate;
INSERT INTO #preview SELECT 'foam', 'tbl_TG_NGP2_Foam_AirInjectFlowRate', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_AirInjectFlowRate
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_AirInjectPressure;
INSERT INTO #preview SELECT 'foam', 'tbl_TG_NGP2_Foam_AirInjectPressure', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_AirInjectPressure
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_NozzleTimeActual;
INSERT INTO #preview SELECT 'foam', 'tbl_TG_NGP2_Foam_NozzleTimeActual', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_NozzleTimeActual
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Foam_SolutionFlowRate;
INSERT INTO #preview SELECT 'foam', 'tbl_TG_NGP2_Foam_SolutionFlowRate', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Foam_SolutionFlowRate
     WHERE t_stamp > @m AND t_stamp < @cutoff);

-- ── Schubert ──────────────────────────────────────────────────────────────────
SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Schubert_ActiveProduct;
INSERT INTO #preview SELECT 'schubert', 'tbl_TG_NGP2_Schubert_ActiveProduct', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Schubert_ActiveProduct
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Schubert_AlarmStack;
INSERT INTO #preview SELECT 'schubert', 'tbl_TG_NGP2_Schubert_AlarmStack', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Schubert_AlarmStack
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Schubert_MachineMode;
INSERT INTO #preview SELECT 'schubert', 'tbl_TG_NGP2_Schubert_MachineMode', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Schubert_MachineMode
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_NGP2_Schubert_MachineState;
INSERT INTO #preview SELECT 'schubert', 'tbl_TG_NGP2_Schubert_MachineState', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_NGP2_Schubert_MachineState
     WHERE t_stamp > @m AND t_stamp < @cutoff);

-- ── Result ────────────────────────────────────────────────────────────────────
SELECT
    grp         AS sub_system,
    table_name,
    local_max,
    pending     AS rows_to_insert,
    CASE
        WHEN pending = 0 THEN 'up-to-date'
        WHEN local_max IS NULL THEN 'EMPTY — needs baseline export first'
        ELSE 'ready'
    END AS status
FROM #preview
ORDER BY grp, table_name;

SELECT SUM(pending) AS total_rows_to_insert FROM #preview;

DROP TABLE #preview;
