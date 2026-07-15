-- ============================================================
-- HU3 Weekly Sync  |  db_ProcessData → hu3_may
-- ============================================================
-- HOW TO USE:
--   1. Update @cutoff to the new end-of-week boundary (shift start time 07:07).
--   2. Connect to the LOCAL SQL Server instance in DBeaver (hu3_may host).
--      Do NOT connect to db_ProcessData — the linked server handles that.
--      Uses the same [db_ProcessData] linked server as ngp2_sync.sql.
--      If not yet set up, run ngp2_linked_server_setup.sql once.
--   3. Run the whole script. Each table auto-detects its local MAX and inserts
--      only rows that are newer. Re-running is safe — no duplicates possible
--      because the anchor uses strict > (not >=).
--   4. Check the PRINT output in DBeaver's Log/Messages tab for row counts.
--
-- Run hu3_sync_preview.sql first if you want a dry-run row count.
-- ============================================================

USE [hu3_may];

-- ALTER TABLE dbo.tbl_SCP_HU3_DowntimeOverlays ALTER COLUMN DT_Attribution NVARCHAR(255);
-- ── UPDATE THIS EACH WEEK ──────────────────────────────────────────────────
DECLARE @cutoff DATETIME = '2026-06-20 07:07:00';
-- ── OPTIONAL: force start date for StationCycleMinMaxResults ───────────────
--   NULL  = resume from local MAX(t_stamp)  (normal weekly use)
--   date  = skip history before this point  (e.g. '2026-06-01 00:00:00')
DECLARE @scmmr_from_override DATETIME = '2026-06-13 07:07:00';
-- ──────────────────────────────────────────────────────────────────────────

DECLARE @from   DATETIME;
DECLARE @n      INT;
DECLARE @total  INT = 0;

PRINT '=== HU3 Sync started: cutoff = ' + CONVERT(VARCHAR, @cutoff, 120) + ' ===';
PRINT '';

-- ── Core ──────────────────────────────────────────────────────────────────────

-- StatusBlocks uses Start_TS / End_TS (not t_stamp)
SELECT @from = MAX(Start_TS) FROM dbo.tbl_SCP_HU3_StatusBlocks WHERE Start_TS < @cutoff;
INSERT INTO dbo.tbl_SCP_HU3_StatusBlocks
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_SCP_HU3_StatusBlocks
    WHERE Start_TS > @from AND Start_TS < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_SCP_HU3_StatusBlocks                ' + CAST(@n AS VARCHAR) + ' rows  (from ' + ISNULL(CONVERT(VARCHAR, @from, 120), 'NULL') + ')';

-- DowntimeOverlays anchors on Start_TS (has both Start_TS and t_stamp; Start_TS matches StatusBlocks pattern)
SELECT @from = MAX(Start_TS) FROM dbo.tbl_SCP_HU3_DowntimeOverlays WHERE Start_TS < @cutoff;
INSERT INTO dbo.tbl_SCP_HU3_DowntimeOverlays
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_SCP_HU3_DowntimeOverlays
    WHERE Start_TS > @from AND Start_TS < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_SCP_HU3_DowntimeOverlays            ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_FOFchanges_WithReasonCode;
INSERT INTO dbo.tbl_TG_HU3_HH_FOFchanges_WithReasonCode
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_HU3_HH_FOFchanges_WithReasonCode
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_HU3_HH_FOFchanges_WithReasonCode   ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_LiveOEE;
INSERT INTO dbo.tbl_TG_HU3_HH_LiveOEE
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_HU3_HH_LiveOEE
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_HU3_HH_LiveOEE                   ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_MachineState;
INSERT INTO dbo.tbl_TG_HU3_HH_MachineState
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_HU3_HH_MachineState
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_HU3_HH_MachineState              ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_OutputStats;
INSERT INTO dbo.tbl_TG_HU3_HH_OutputStats
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_HU3_HH_OutputStats
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_HU3_HH_OutputStats               ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_PartsPerMinute;
INSERT INTO dbo.tbl_TG_HU3_HH_PartsPerMinute
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_HU3_HH_PartsPerMinute
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_HU3_HH_PartsPerMinute            ' + CAST(@n AS VARCHAR) + ' rows';

PRINT '';

-- ── Quality / Operator ────────────────────────────────────────────────────────
PRINT '-- Quality / Operator --';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_SCP_HU3_OpMarkedDefects;
INSERT INTO dbo.tbl_SCP_HU3_OpMarkedDefects
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_SCP_HU3_OpMarkedDefects
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_SCP_HU3_OpMarkedDefects             ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_OperatorActions;
INSERT INTO dbo.tbl_TG_HU3_HH_OperatorActions
    SELECT * FROM [db_ProcessData].[db_ProcessData].dbo.tbl_TG_HU3_HH_OperatorActions
    WHERE t_stamp > @from AND t_stamp < @cutoff;
SET @n = @@ROWCOUNT; SET @total += @n;
PRINT 'tbl_TG_HU3_HH_OperatorActions           ' + CAST(@n AS VARCHAR) + ' rows';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_StationCycleMinMaxResults;
-- All 10 station columns are xml(max). ~9 KB binary XML per row = 100-160 MB/day as NVARCHAR(MAX).
-- Process in 1-hour batches (~15 MB each) to keep each OPENQUERY fast.
DECLARE @scmmr_start DATETIME    = ISNULL(@scmmr_from_override, ISNULL(@from, '2000-01-01'));
DECLARE @scmmr_end   DATETIME;
DECLARE @scmmr_fs    VARCHAR(30);
DECLARE @scmmr_cs    VARCHAR(30);
DECLARE @q           CHAR(1)     = CHAR(39);
DECLARE @scmmr_sql   NVARCHAR(MAX);
DECLARE @scmmr_n     INT;
DECLARE @scmmr_total INT         = 0;

WHILE @scmmr_start < @cutoff
BEGIN
    SET @scmmr_end = CASE WHEN DATEADD(HOUR, 1, @scmmr_start) < @cutoff
                          THEN DATEADD(HOUR, 1, @scmmr_start)
                          ELSE @cutoff END;
    SET @scmmr_fs  = CONVERT(VARCHAR(30), @scmmr_start, 121);
    SET @scmmr_cs  = CONVERT(VARCHAR(30), @scmmr_end,   121);
    SET @scmmr_sql =
        N'INSERT INTO dbo.tbl_TG_HU3_HH_StationCycleMinMaxResults'
      + N' (ID, Stn005, Stn020, Stn022, Stn026, Stn070, Stn074, Stn076, Stn148, Stn149, Stn410, t_stamp, quality_code)'
      + N' SELECT ID,'
      + N' CAST(Stn005 AS XML), CAST(Stn020 AS XML), CAST(Stn022 AS XML), CAST(Stn026 AS XML),'
      + N' CAST(Stn070 AS XML), CAST(Stn074 AS XML), CAST(Stn076 AS XML), CAST(Stn148 AS XML),'
      + N' CAST(Stn149 AS XML), CAST(Stn410 AS XML),'
      + N' t_stamp, quality_code'
      + N' FROM OPENQUERY([db_ProcessData], ' + @q
      + N'SELECT ID,'
      + N' CAST(Stn005 AS NVARCHAR(MAX)) AS Stn005, CAST(Stn020 AS NVARCHAR(MAX)) AS Stn020,'
      + N' CAST(Stn022 AS NVARCHAR(MAX)) AS Stn022, CAST(Stn026 AS NVARCHAR(MAX)) AS Stn026,'
      + N' CAST(Stn070 AS NVARCHAR(MAX)) AS Stn070, CAST(Stn074 AS NVARCHAR(MAX)) AS Stn074,'
      + N' CAST(Stn076 AS NVARCHAR(MAX)) AS Stn076, CAST(Stn148 AS NVARCHAR(MAX)) AS Stn148,'
      + N' CAST(Stn149 AS NVARCHAR(MAX)) AS Stn149, CAST(Stn410 AS NVARCHAR(MAX)) AS Stn410,'
      + N' t_stamp, quality_code'
      + N' FROM db_ProcessData.dbo.tbl_TG_HU3_HH_StationCycleMinMaxResults'
      + N' WHERE t_stamp > ' + @q+@q + @scmmr_fs + @q+@q
      + N' AND t_stamp < '   + @q+@q + @scmmr_cs  + @q+@q + @q + N');'
      + N' SET @rows = @@ROWCOUNT';
    EXEC sp_executesql @scmmr_sql, N'@rows INT OUTPUT', @rows = @scmmr_n OUTPUT;
    SET @scmmr_total += @scmmr_n;
    PRINT '  StationCycleMinMaxResults ' + @scmmr_fs + ' -> ' + @scmmr_cs + ': ' + CAST(@scmmr_n AS VARCHAR) + ' rows';
    SET @scmmr_start = @scmmr_end;
END
SET @n = @scmmr_total; SET @total += @n;
PRINT 'tbl_TG_HU3_HH_StationCycleMinMaxResults  ' + CAST(@scmmr_total AS VARCHAR) + ' rows total';

SELECT @from = MAX(t_stamp) FROM dbo.tbl_TG_HU3_HH_BatchReport;
-- BatchReportXML is xml(max); same OPENQUERY pattern as StationCycleMinMaxResults above.
DECLARE @br_fs  VARCHAR(30) = ISNULL(CONVERT(VARCHAR(30), @from,   121), '2000-01-01 00:00:00');
DECLARE @br_cs  VARCHAR(30) = CONVERT(VARCHAR(30), @cutoff, 121);
DECLARE @br_q   CHAR(1)     = CHAR(39);
DECLARE @br_sql NVARCHAR(MAX) =
    N'INSERT INTO dbo.tbl_TG_HU3_HH_BatchReport (ID, BatchReportXML, t_stamp, quality_code)'
  + N' SELECT ID, CAST(BatchReportXML AS XML), t_stamp, quality_code'
  + N' FROM OPENQUERY([db_ProcessData], ' + @br_q
  + N'SELECT ID, CAST(BatchReportXML AS NVARCHAR(MAX)) AS BatchReportXML, t_stamp, quality_code'
  + N' FROM db_ProcessData.dbo.tbl_TG_HU3_HH_BatchReport'
  + N' WHERE t_stamp > ' + @br_q+@br_q + @br_fs + @br_q+@br_q
  + N' AND t_stamp < '   + @br_q+@br_q + @br_cs  + @br_q+@br_q + @br_q + N');'
  + N' SET @rows = @@ROWCOUNT';
DECLARE @br_n INT;
EXEC sp_executesql @br_sql, N'@rows INT OUTPUT', @rows = @br_n OUTPUT;
SET @n = @br_n; SET @total += @n;
PRINT 'tbl_TG_HU3_HH_BatchReport               ' + CAST(@n AS VARCHAR) + ' rows  (from ' + @br_fs + ')';

PRINT '';
PRINT '=== Sync complete. Total rows inserted: ' + CAST(@total AS VARCHAR) + ' ===';
