#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
ref: orabbix配置参数: https://www.jianshu.com/p/93bda2a2b656
用法
crontab:
# oracle_monitor
*/5 * * * *  python /{your_dir}/db_monitor/oracle_monitor.py >/dev/null 2>&1    # 每5分钟检测一次
"""
import os
import time
import logging.handlers
from functools import reduce
import cx_Oracle
from elasticsearch import Elasticsearch, helpers
from elasticsearch.exceptions import *
import sys

reload(sys)
sys.setdefaultencoding('utf-8')

CURRENT_DIR = reduce(lambda x, y: os.path.dirname(x), range(1), os.path.abspath(__file__))
LOG_DIR = os.path.join(CURRENT_DIR, 'logs')
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)
LOG_FILE = os.path.join(LOG_DIR, 'oracle_monitor.log')

handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1024 * 1024, backupCount=5)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

logger = logging.getLogger('oracle_monitor')
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

# ----- 需要修改的参数 -----
# oracle 监测
db_username = 'system'
db_password = 'password'
db_host = '192.168.10.182'
db_port = '1521'
db_service_name = 'orcl'
# es 持久化
es_host = '127.0.0.1'
token = '4a859fff6e5c4521aab187eee1cfceb8'
appname = 'dbmonitor'
doc_type = 'oracle'
index_name = 'cc-{appname}-{doc_type}-{token}-{suffix}'.format(
    appname=appname,
    doc_type=doc_type,
    token=token,
    suffix=time.strftime('%Y.%m.%d')
)
# ------------------------

query_dict = dict(
    dbfilesize="""
    select sum(bytes/1024/1024/10), 'FM99999999999999990' retvalue from dba_data_files
    """,
    dbsize="""
    SELECT sum(  NVL(a.bytes/1024/1024/10 - NVL(f.bytes/1024/1024/10, 0), 0)), 'FM99999999999999990' retvalue 
    FROM sys.dba_tablespaces d,
    (select tablespace_name, sum(bytes) bytes from dba_data_files group by tablespace_name) a,
    (select tablespace_name, sum(bytes) bytes from dba_free_space group by tablespace_name) f
    WHERE d.tablespace_name = a.tablespace_name(+) AND d.tablespace_name = f.tablespace_name(+)
    AND NOT (d.extent_management like 'LOCAL' AND d.contents like 'TEMPORARY')
    """,
    rman_check_status="""
    select ' DB NAME->'||DB_NAME||'- ROW TYPE->'||ROW_TYPE||'- START TIME->'||to_number(start_time, 'Dy DD-Mon-YYYY HH24:MI:SS') ||'- END TIME->'||to_number(end_time, 'Dy DD-Mon-YYYY HH24:MI:SS')||'- MBYTES PROCESSED->'||MBYTES_PROCESSED||'- OBJECT TYPE->'||OBJECT_TYPE||'- STATUS->'||STATUS||'- OUTPUT DEVICE->'||OUTPUT_DEVICE_TYPE||'- INPUT MB->'||INPUT_BYTES/1048576||'- OUT MB'||OUTPUT_BYTES/1048576 
    FROM rc_rman_status WHERE  start_time > SYSDATE - 1 AND ( STATUS like '%FAILED%' OR  STATUS like '%ERROR%') 
    ORDER  BY END_TIME
    """,
    uptime="""
    select (sysdate-startup_time)*86400, 'FM99999999999999990' retvalue from v$instance
    """,
    users_locked="""
    SELECT username||' '|| lock_date ||' '|| account_status FROM dba_users where ACCOUNT_STATUS like 'EXPIRED(GRACE)' or ACCOUNT_STATUS like 'LOCKED(TIMED)'
    """,
    archive="""
    select round(A.LOGS*B.AVG/1024/1024/10) from ( SELECT COUNT (*)  LOGS FROM V$LOG_HISTORY WHERE FIRST_TIME >= (sysdate -10/60/24)) A, ( SELECT Avg(BYTES) AVG,  Count(1), Max(BYTES) Max_Bytes, Min(BYTES) Min_Bytes  FROM  v$log) B
    """,
    archive_race_condition="""
    select value from v$parameter where name='log_archive_start'
    """,
    audit="""
    select username "username", timestamp,'DD-MON-YYYY HH24:MI:SS' "time_stamp", action_name "statement", 
    os_username "os_username", userhost "userhost", 
    returncode||decode(returncode,'1004','-Wrong Connection','1005','-NULL Password','1017','-Wrong Password','1045','-Insufficient Priviledge','0','-Login Accepted','--') "returncode"
    from sys.dba_audit_session
    where (sysdate - timestamp)*24 < 1 and returncode <> 0
    order by timestamp
    """,
    dbblockgets="""
    select sum(decode(name,'db block gets', value,0)) "block_gets" FROM v$sysstat
    """,
    dbconsistentgets="""
    select sum(decode(name,'consistent gets', value,0)) "consistent_gets" FROM v$sysstat
    """,
    dbhitratio=
    """
    select (
    sum(decode(name,'consistent gets', value,0)) + sum(decode(name,'db block gets', value,0)) - sum(decode(name,'physical reads', value,0))
    ) / (sum(decode(name,'consistent gets', value,0)) + sum(decode(name,'db block gets', value,0)) ) * 100 "hit_ratio"
    FROM v$sysstat
    """,
    dbphysicalread="""
    select sum(decode(name,'physical reads', value,0)) "phys_reads" FROM v$sysstat
    """,
    dbversion="""
    select COMP_ID||' '||COMP_NAME||' '||VERSION||' '||STATUS||' <br />' 
    from dba_registry 
    union SELECT ' - SERVERNAME = <b>'||UTL_INADDR.get_host_name ||'</b> - SERVERADDRESS = <b>'||UTL_INADDR.get_host_address||'</b> <br />'
    from dual 
    union SELECT ' - DB_NAME = <b>'||SYS_CONTEXT ('USERENV', 'DB_NAME') ||'</b> - INSTANCE_NAME = <b>' ||SYS_CONTEXT ('USERENV', 'INSTANCE_NAME')||'</b> <br />' 
    FROM dual
    """,
    sqlnotindexed="""
    SELECT 
    SUM(DECODE(NAME, 'table scans (long tables)', VALUE, 0)) 
    / (SUM(DECODE(NAME, 'table scans (long tables)', VALUE, 0))+SUM(DECODE(NAME, 'table scans (short tables)', VALUE, 0))) 
    *100 SQL_NOT_INDEXED 
    FROM V$SYSSTAT WHERE 1=1 AND ( NAME IN ('table scans (long tables)','table scans (short tables)') )
    """,
    hitratio_body="""
    select gethitratio*100 "get_pct" FROM v$librarycache where namespace ='BODY'
    """,
    hitratio_sqlarea="""
    select gethitratio*100 "get_pct" FROM v$librarycache where namespace ='SQL AREA'
    """,
    hitratio_trigger="""
    select gethitratio*100 "get_pct" FROM v$librarycache where namespace ='TRIGGER'
    """,
    hitratio_table_proc="""
    select gethitratio*100 "get_pct" FROM v$librarycache where namespace = 'TABLE/PROCEDURE'
    """,
    lio_block_changes="""
    SELECT SUM(DECODE(NAME,'db block changes',VALUE,0)) FROM V$SYSSTAT WHERE NAME ='db block changes'
    """,
    lio_consistent_read="""
    SELECT sum(decode(name,'consistent gets',value,0)) FROM V$SYSSTAT WHERE NAME ='consistent gets'
    """,
    lio_current_read="""
    SELECT sum(decode(name,'db block gets',value,0)) FROM V$SYSSTAT WHERE NAME ='db block gets'
    """,
    locks="""
    SELECT b.session_id AS sid, NVL(b.oracle_username, '(oracle)') AS username, a.owner AS object_owner, 
    a.object_name, 
    Decode(b.locked_mode, 0, 'None',
                          1, 'Null (NULL)',
                          2, 'Row-S (SS)',
                          3, 'Row-X (SX)',
                          4, 'Share (S)',
                          5, 'S/Row-X (SSX)',
                          6, 'Exclusive (X)',
           b.locked_mode) locked_mode, b.os_user_name 
    FROM dba_objects a, v$locked_object b
    WHERE  a.object_id = b.object_id
    ORDER BY 1, 2, 3, 4
    """,
    maxprocs="""
    select value "maxprocs" from v$parameter where name ='processes'
    """,
    maxsession="""
    select value "maxsess" from v$parameter where name ='sessions'
    """,
    miss_latch="""
    SELECT SUM(misses) FROM V$LATCH
    """,
    pga_aggregate_target="""
    select decode( unit,'bytes', value/1024/1024, value),'999999999.9' value from V$PGASTAT where name in 'aggregate PGA target parameter'
    """,
    pga="""
    select decode( unit,'bytes', value/1024/1024, value),'999999999.9' value from V$PGASTAT where name in 'total PGA inuse'
    """,
    phio_datafile_reads="""
    select sum(decode(name,'physical reads direct',value,0)) FROM V$SYSSTAT where name ='physical reads direct'
    """,
    phio_datafile_writes="""
    select sum(decode(name,'physical writes direct',value,0)) FROM V$SYSSTAT where name ='physical writes direct'
    """,
    phio_redo_writes="""
    select sum(decode(name,'redo writes',value,0)) FROM V$SYSSTAT where name ='redo writes'
    """,
    pinhitratio_body="""
    select pins/(pins+reloads)*100 "pin_hit ratio" FROM v$librarycache where namespace ='BODY'
    """,
    pinhitratio_sqlarea="""
    select pins/(pins+reloads)*100 "pin_hit ratio" FROM v$librarycache where namespace ='SQL AREA'
    """,
    pinhitratio_table_proc="""
    select pins/(pins+reloads)*100 "pin_hit ratio" FROM v$librarycache where namespace ='TABLE/PROCEDURE'
    """,
    pinhitratio_trigger="""
    select pins/(pins+reloads)*100 "pin_hit ratio" FROM v$librarycache where namespace ='TRIGGER'
    """,
    pool_dict_cache="""
    SELECT ROUND(SUM(decode(pool,'shared pool',decode(name,'dictionary cache',(bytes)/(1024*1024),0),0)),2) pool_dict_cache FROM V$SGASTAT
    """,
    pool_free_mem="""
    SELECT ROUND(SUM(decode(pool,'shared pool',decode(name,'free memory',(bytes)/(1024*1024),0),0)),2) pool_free_mem FROM V$SGASTAT
    """,
    pool_lib_cache="""
    SELECT ROUND(SUM(decode(pool,'shared pool',decode(name,'library cache',(bytes)/(1024*1024),0),0)),2) pool_lib_cache FROM V$SGASTAT
    """,
    pool_misc="""
    SELECT ROUND(SUM(decode(pool,'shared pool',decode(name,'library cache',0,'dictionary cache',0,'free memory',0,'sql area', 0,(bytes)/(1024*1024)),0)),2) pool_misc FROM V$SGASTAT
    """,
    pool_sql_area="""
    SELECT ROUND(SUM(decode(pool,'shared pool',decode(name,'sql area',(bytes)/(1024*1024),0),0)),2) pool_sql_area FROM V$SGASTAT
    """,
    procnum="""
    select count(*) "procnum" from v$process
    """,
    session_active="""
    select count(*) from v$session where TYPE!='BACKGROUND' and status='ACTIVE'
    """,
    session_inactive="""
    select SUM(Decode(Type, 'BACKGROUND', 0, Decode(Status, 'ACTIVE', 0, 1))) FROM V$SESSION
    """,
    session="""
    select count(*) from v$session
    """,
    session_system="""
    select SUM(Decode(Type, 'BACKGROUND', 1, 0)) system_sessions FROM V$SESSION
    """,
    sga_buffer_cache="""
    SELECT ROUND(SUM(decode(pool,NULL,decode(name,'db_block_buffers',(bytes)/(1024*1024),'buffer_cache',(bytes)/(1024*1024),0),0)),2) sga_bufcache FROM V$SGASTAT
    """,
    sga_fixed="""
    SELECT ROUND(SUM(decode(pool,NULL,decode(name,'fixed_sga',(bytes)/(1024*1024),0),0)),2) sga_fixed FROM V$SGASTAT
    """,
    sga_java_pool="""
    SELECT ROUND(SUM(decode(pool,'java pool',(bytes)/(1024*1024),0)),2) sga_jpool FROM V$SGASTAT
    """,
    sga_large_pool="""
    SELECT ROUND(SUM(decode(pool,'large pool',(bytes)/(1024*1024),0)),2) sga_lpool FROM V$SGASTAT
    """,
    sga_log_buffer="""
    SELECT ROUND(SUM(decode(pool,NULL,decode(name,'log_buffer',(bytes)/(1024*1024),0),0)),2) sga_lbuffer FROM V$SGASTAT
    """,
    sga_shared_pool="""
    SELECT ROUND(SUM(decode(pool,'shared pool',decode(name,'library cache',0,'dictionary cache',0,'free memory',0,'sql area',0,(bytes)/(1024*1024)),0)),2) pool_misc FROM V$SGASTAT
    """,
    tbl_space="""
    SELECT * FROM (
        select '- Tablespace ->',t.tablespace_name ktablespace, 
                '- Type->',substr(t.contents, 1, 1) tipo,
                '- Used(MB)->',trunc((d.tbs_size-nvl(s.free_space, 0))/1024/1024) ktbs_em_uso,
                '- ActualSize(MB)->',trunc(d.tbs_size/1024/1024) ktbs_size,
                '- MaxSize(MB)->',trunc(d.tbs_maxsize/1024/1024) ktbs_maxsize,
                '- FreeSpace(MB)->',trunc(nvl(s.free_space, 0)/1024/1024) kfree_space,
                '- Space->',trunc((d.tbs_maxsize - d.tbs_size + nvl(s.free_space, 0))/1024/1024) kspace,
                '- Perc->',decode(d.tbs_maxsize, 0, 0, trunc((d.tbs_size-nvl(s.free_space, 0))*100/d.tbs_maxsize)) kperc
        from (
                select SUM(bytes) tbs_size, SUM(decode(sign(maxbytes - bytes), -1, bytes, maxbytes)) tbs_maxsize, tablespace_name tablespace
                from (
                    select nvl(bytes, 0) bytes, nvl(maxbytes, 0) maxbytes, tablespace_name from dba_data_files
                    union all
                    select nvl(bytes, 0) bytes, nvl(maxbytes, 0) maxbytes, tablespace_name from dba_temp_files
                )
                    group by tablespace_name
        ) d, (select SUM(bytes) free_space, tablespace_name tablespace from dba_free_space group by tablespace_name) s, dba_tablespaces t
              where t.tablespace_name = d.tablespace(+) and
                    t.tablespace_name = s.tablespace(+)
              order by 8)
              where kperc > 93
              and tipo <>'T'
              and tipo <>'U'
    """,
    userconn="""
    select count(username) from v$session where username is not null
    """,
    waits_controfileio="""
    SELECT sum(decode(event,'control file sequential read', total_waits, 'control file single write', total_waits, 'control file parallel write',total_waits,0)) ControlFileIO FROM V$system_event WHERE 1=1 AND event not in ( 'SQL*Net message from client', 'SQL*Net more data from client','pmon timer', 'rdbms ipc message', 'rdbms ipc reply', 'smon timer')
    """,
    waits_directpath_read="""
    SELECT sum(decode(event,'direct path read',total_waits,0)) DirectPathRead FROM V$system_event WHERE 1=1 AND event not in ('SQL*Net message from ', 'SQL*Net more data from client','pmon timer', 'rdbms ipc message', 'rdbms ipc reply', 'smon timer')
    """,
    waits_file_io="""
    SELECT sum(decode(event,'file identify',total_waits, 'file open',total_waits,0)) FileIO FROM V$system_event WHERE 1=1 AND event not in ('SQL*Net message from client',   'SQL*Net more data from client', 'pmon timer', 'rdbms ipc message', 'rdbms ipc reply', 'smon timer')
    """,
    waits_latch="""
    SELECT sum(decode(event,'control file sequential read', 
                total_waits, 'control file single write', total_waits, 'control file parallel write',total_waits,0)
            ) ControlFileIO
    FROM V$system_event WHERE 1=1 AND event not in (
                      'SQL*Net message from client',
                      'SQL*Net more data from client',
                      'pmon timer', 'rdbms ipc message',
                      'rdbms ipc reply', 'smon timer')
    """,
    waits_logwrite="""
    SELECT sum(decode(event,'log file single write',total_waits, 'log file parallel write',total_waits,0)) LogWrite
    FROM V$system_event WHERE 1=1 AND event not in (
                      'SQL*Net message from client',
                      'SQL*Net more data from client',
                      'pmon timer', 'rdbms ipc message',
                      'rdbms ipc reply', 'smon timer')
    """,
    waits_multiblock_read="""
    SELECT sum(decode(event,'db file scattered read',total_waits,0)) MultiBlockRead
    FROM V$system_event WHERE 1=1 AND event not in (
                      'SQL*Net message from client',
                      'SQL*Net more data from client',
                      'pmon timer', 'rdbms ipc message',
                      'rdbms ipc reply', 'smon timer')
    """,
    waits_other="""
    SELECT sum(decode(event,'control file sequential read',0,'control file single write',0,'control file parallel write',0,'db file sequential read',0,'db file scattered read',0,'direct path read',0,'file identify',0,'file open',0,'SQL*Net message to client',0,'SQL*Net message to dblink',0, 'SQL*Net more data to client',0,'SQL*Net more data to dblink',0, 'SQL*Net break/reset to client',0,'SQL*Net break/reset to dblink',0, 'log file single write',0,'log file parallel write',0,total_waits)
            ) 
            Other FROM V$system_event WHERE 1=1 AND event not in ('SQL*Net message from client', 'SQL*Net more data from client', 'pmon timer', 'rdbms ipc message',  'rdbms ipc reply', 'smon timer')
    """,
    waits_singleblock_read="""
    SELECT sum(decode(event,'db file sequential read',total_waits,0)) SingleBlockRead 
    FROM V$system_event WHERE 1=1 AND event not in (
                        'SQL*Net message from client',
                        'SQL*Net more data from client',
                        'pmon timer', 'rdbms ipc message',
                        'rdbms ipc reply', 'smon timer')
    """,
    waits_sqlnet="""
    SELECT sum(decode(event,'SQL*Net message to client',total_waits,'SQL*Net message to dblink',total_waits,'SQL*Net more data to client',total_waits,'SQL*Net more data to dblink',total_waits,'SQL*Net break/reset to client',total_waits,'SQL*Net break/reset to dblink',total_waits,0)) 
    SQLNET FROM V$system_event WHERE 1=1
    AND event not in ( 'SQL*Net message from client','SQL*Net more data from client','pmon timer','rdbms ipc message','rdbms ipc reply', 'smon timer')
    """,
    dg_error="""
    SELECT ERROR_CODE, SEVERITY, MESSAGE, TIMESTAMP, 'DD-MON-RR HH24:MI:SS' TIMESTAMP FROM V$DATAGUARD_STATUS WHERE CALLOUT='YES' AND TIMESTAMP > SYSDATE-1
    """,
    dg_sequence_number="""
    SELECT MAX (sequence#) FROM v$log_history
    """,
    dg_sequence_number_stby="""
    select max(sequence#) from v$archived_log
    """,
    # extra
    instance_status="select status from v$instance",
    database_status="select open_mode from v$database"

)


def catch_exception(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except cx_Oracle.DatabaseError as e:
            logger.error('%s\n' % e)
            sys.exit()
        except TransportError as e:
            if isinstance(e, ConnectionTimeout):
                logger.error('Elasticsearch read timed out\n')
            elif isinstance(e, ConnectionError):
                logger.error('Elasticsearch connection refused\n')
            else:
                logger.error('System err\n')
            sys.exit()
        except Exception as e:
            logger.error('Error execute: %s' % func.__name__)
            logger.error('%s\n' % e)
            sys.exit()
        finally:
            pass

    return wrapper


@catch_exception
def execute_query():
    conn = cx_Oracle.connect(db_username, db_password, '{db_host}:{db_port}/{db_service_name}'.format(
        db_host=db_host,
        db_port=db_port,
        db_service_name=db_service_name
    ))
    cur = conn.cursor()
    query_list = query_dict.keys()
    query_result = {}
    for query_item in query_list:
        try:
            if query_item in ['dbversion']:
                rows = cur.execute(query_dict[query_item]).fetchall()
                query_result[query_item] = ''.join([row[0] for row in rows]) if rows else rows
            else:
                row = cur.execute(query_dict[query_item]).fetchone()
                query_result[query_item] = row[0] if row else row
        except cx_Oracle.DatabaseError as e:
            logger.error(query_item)
            logger.error(e)
            # query_result[query_item] = e.message.message
            # query_result[query_item] = str(e)
            query_result[query_item] = None
        except Exception as e:
            logger.error(query_item)
            logger.error(e)
            query_result[query_item] = None
    cur.close()
    conn.close()
    return query_result


@catch_exception
def es_persist(doc_list):
    # create index if not exist
    es = Elasticsearch(es_host)
    if not es.indices.exists(index=index_name):
        settings = {
            'index': {
                'number_of_shards': 1,
                'number_of_replicas': 0,
                'refresh_interval': '1s'
            }
        }
        mappings = {
            doc_type: {
                'dynamic_templates': [
                    {
                        'string_fields': {
                            'match': '*',
                            'match_mapping_type': 'string',
                            'mapping': {
                                'type': 'keyword'
                            }
                        }
                    }
                ],
                'properties': {
                    '@timestamp': {
                        'format': 'strict_date_optional_time||epoch_millis',
                        'type': 'date'
                    }
                }
            }
        }
        create_res = es.indices.create(index=index_name,
                                       body=dict(settings=settings,
                                                 mappings=mappings)
                                       )
        if create_res.get('acknowledged'):
            logger.info('Create {index_name} successfully'.format(index_name=index_name))

    # bulk data
    actions = []
    for doc in doc_list:
        _source = {
            'appname': appname,
            'type': doc_type,
            'topic': appname,
            '@timestamp': int(round(time.time() * 1000)),
            'oracle': doc
        }
        action = {
            '_op_type': 'index',
            '_index': index_name,
            '_type': doc_type,
            '_source': _source
        }
        actions.append(action)
    success, failed = helpers.bulk(client=es, actions=actions)
    return success, failed


if __name__ == "__main__":
    query_result = execute_query()
    success, failed = es_persist([query_result])
    logger.info('Bulk success: %s, failed: %s\n' % (success, failed))
