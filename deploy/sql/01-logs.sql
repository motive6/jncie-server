CREATE TABLE IF NOT EXISTS SystemEvents (
  ID bigint unsigned NOT NULL AUTO_INCREMENT,
  CustomerID bigint DEFAULT NULL,
  ReceivedAt datetime DEFAULT NULL,
  DeviceReportedTime datetime DEFAULT NULL,
  Facility smallint DEFAULT NULL,
  Priority smallint DEFAULT NULL,
  FromHost varchar(60) DEFAULT NULL,
  FromHostIP varchar(60) DEFAULT NULL,
  ProgramName varchar(60) DEFAULT NULL,
  InfoUnitID int DEFAULT 0,
  SysLogTag varchar(60) NOT NULL DEFAULT '',
  EventLogID int DEFAULT NULL,
  EventSource varchar(60) DEFAULT NULL,
  EventCategory int DEFAULT NULL,
  EventID int DEFAULT NULL,
  EventSourceName varchar(60) DEFAULT NULL,
  Message text,
  SupplementaryInfo text,
  ProcessID varchar(10) DEFAULT NULL,
  PRIMARY KEY (ID),
  KEY idx_svcadmin_received (ReceivedAt),
  KEY idx_svcadmin_host (FromHost),
  KEY idx_svcadmin_program (ProgramName),
  KEY idx_svcadmin_priority (Priority)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS SystemEventsProperties (
  ID bigint unsigned NOT NULL AUTO_INCREMENT,
  SystemEventID bigint unsigned DEFAULT NULL,
  ParamName varchar(50) DEFAULT NULL,
  ParamValue text,
  PRIMARY KEY (ID),
  KEY idx_svcadmin_event (SystemEventID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
