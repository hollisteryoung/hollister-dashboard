-- ============================================================
-- HU3 Sync Preview  |  db_ProcessData → hu3_may
-- ============================================================
-- Run this BEFORE hu3_sync.sql to check what would be inserted.
-- Connect to the LOCAL SQL Server instance in DBeaver (hu3_may host).
-- Uses the same [db_ProcessData] linked server as ngp2_sync_preview.sql.
-- If not yet set up, run ngp2_linked_server_setup.sql once.
-- Output: one row per table showing local max date and pending row count.
-- ============================================================

USE [hu3_may];


--SELECT MAX(t_stamp), COUNT(*) FROM dbo.tbl_TG_HU3_HH_StationCycleMinMaxResults;

-- ── Update this each week ──────────────────────────────────────────────────
DECLARE @cutoff DATETIME = '2026-06-20 07:07:00';
-- ──────────────────────────────────────────────────────────────────────────

CREATE TABLE #preview (
    grp         NVARCHAR(20),
    table_name  NVARCHAR(200),
    ts_col      NVARCHAR(50),
    local_max   DATETIME,
    pending     INT
);

DECLARE @m DATETIME;

-- ── Core ──────────────────────────────────────────────────────────────────────

SELECT @m = MAX(Start_TS) FROM dbo.tbl_SCP_HU3_StatusBlocks WHERE Start_TS < @cutoff;
INSERT INTO #preview SELECT 'core', 'tbl_SCP_HU3_StatusBlocks', 'Start_TS', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_SCP_HU3_StatusBlocks
     WHERE Start_TS > @m AND Start_TS < @cutoff);

SELECT @m = MAX(Start_TS) FROM dbo.tbl_SCP_HU3_DowntimeOverlays WHERE Start_TS < @cutoff;
INSERT INTO #preview SELECT 'core', 'tbl_SCP_HU3_DowntimeOverlays', 'Start_TS', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_SCP_HU3_DowntimeOverlays
     WHERE Start_TS > @m AND Start_TS < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_FOFchanges_WithReasonCode;
INSERT INTO #preview SELECT 'core', 'tbl_TG_HU3_HH_FOFchanges_WithReasonCode', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_HU3_HH_FOFchanges_WithReasonCode
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_LiveOEE;
INSERT INTO #preview SELECT 'core', 'tbl_TG_HU3_HH_LiveOEE', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_HU3_HH_LiveOEE
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_MachineState;
INSERT INTO #preview SELECT 'core', 'tbl_TG_HU3_HH_MachineState', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_HU3_HH_MachineState
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_OutputStats;
INSERT INTO #preview SELECT 'core', 'tbl_TG_HU3_HH_OutputStats', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_HU3_HH_OutputStats
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_PartsPerMinute;
INSERT INTO #preview SELECT 'core', 'tbl_TG_HU3_HH_PartsPerMinute', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_HU3_HH_PartsPerMinute
     WHERE t_stamp > @m AND t_stamp < @cutoff);

-- ── Quality / Operator ────────────────────────────────────────────────────────

SELECT @m = MAX(t_stamp) FROM dbo.tbl_SCP_HU3_OpMarkedDefects;
INSERT INTO #preview SELECT 'quality', 'tbl_SCP_HU3_OpMarkedDefects', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_SCP_HU3_OpMarkedDefects
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_OperatorActions;
INSERT INTO #preview SELECT 'quality', 'tbl_TG_HU3_HH_OperatorActions', 't_stamp', @m,
    (SELECT COUNT(*) FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_HU3_HH_OperatorActions
     WHERE t_stamp > @m AND t_stamp < @cutoff);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_StationCycleMinMaxResults;
-- xml(max) columns block distributed COUNT; push query to remote via dynamic OPENQUERY
DECLARE @scmmr_fs2 VARCHAR(30) = ISNULL(CONVERT(VARCHAR(30), @m,      120), '2000-01-01 00:00:00');
DECLARE @scmmr_cs2 VARCHAR(30) = CONVERT(VARCHAR(30), @cutoff, 120);
DECLARE @q2        CHAR(1)     = CHAR(39);
DECLARE @scmmr_prv NVARCHAR(MAX) =
    N'INSERT INTO #preview SELECT ''quality'', ''tbl_TG_HU3_HH_StationCycleMinMaxResults'', ''t_stamp'', '
  + @q2 + @scmmr_fs2 + @q2
  + N', cnt FROM OPENQUERY([db_ProcessData], ' + @q2
  + N'SELECT COUNT(*) AS cnt FROM db_ProcessData.dbo.tbl_TG_HU3_HH_StationCycleMinMaxResults'
  + N' WHERE t_stamp > ' + @q2 + @q2 + @scmmr_fs2 + @q2 + @q2
  + N' AND t_stamp < '   + @q2 + @q2 + @scmmr_cs2  + @q2 + @q2
  + @q2 + N')';
EXEC(@scmmr_prv);

SELECT @m = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_BatchReport;
-- BatchReportXML is xml(max); push COUNT to remote via dynamic OPENQUERY
DECLARE @br_fs2 VARCHAR(30) = ISNULL(CONVERT(VARCHAR(30), @m,      120), '2000-01-01 00:00:00');
DECLARE @br_cs2 VARCHAR(30) = CONVERT(VARCHAR(30), @cutoff, 120);
DECLARE @br_q2  CHAR(1)     = CHAR(39);
DECLARE @br_prv NVARCHAR(MAX) =
    N'INSERT INTO #preview SELECT ''quality'', ''tbl_TG_HU3_HH_BatchReport'', ''t_stamp'', '
  + @br_q2 + @br_fs2 + @br_q2
  + N', cnt FROM OPENQUERY([db_ProcessData], ' + @br_q2
  + N'SELECT COUNT(*) AS cnt FROM db_ProcessData.dbo.tbl_TG_HU3_HH_BatchReport'
  + N' WHERE t_stamp > ' + @br_q2 + @br_q2 + @br_fs2 + @br_q2 + @br_q2
  + N' AND t_stamp < '   + @br_q2 + @br_q2 + @br_cs2  + @br_q2 + @br_q2
  + @br_q2 + N')';
EXEC(@br_prv);

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
