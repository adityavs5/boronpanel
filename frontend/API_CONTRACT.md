# Forgehost JSON API Contract (reference for SPA wiring)

Access legend: **[ADMIN]** admin only · **[ACCT]** owner or admin · **[DOM]** domain owner · **[SELF]** owner only (admin rejected).
All auth via signed `fh_session` cookie (send `withCredentials`). Datetimes are ISO-8601 or null.
`GET /api/v1/accounts` returns a BARE array; every other list wraps in a named key.

## accounts — /api/v1/accounts
- POST / [ADMIN] {username, primary_domain?, php_version?, quota_soft_mb?, quota_hard_mb?, password?} → account
- GET / [ADMIN] → [{id,username,status,primary_domain}]
- GET /{username} [ACCT] → account {id,username,status,uid,gid,primary_domain,php_version,quota_soft_mb,quota_hard_mb,cpu_pct,mem_mb,io_mb,pids_max,last_error,created_at,suspended_at,terminated_at}
- POST /{username}/suspend|unsuspend|terminate [ADMIN] → status
- PATCH /{username}/php-version [ACCT] {php_version}
- PATCH /{username}/limits [ADMIN] {cpu_pct,mem_mb,io_mb,pids_max}
- GET /{username}/namespace [ACCT] → {username,uid,min_uid,eligible,explicitly_disabled,enabled}
- PATCH /{username}/namespace [ADMIN] {enabled}
- POST /namespace/bulk-enable [ADMIN] → job; GET /namespace/bulk-enable/{job_id} [ADMIN]

## domains — /api/v1/accounts/{username}/domains  [ACCT]
- GET → {domains:[{id,account_id,domain,kind,docroot,ssl_status,ssl_is_wildcard,php_version,created_at}]}
- POST {domain,kind="addon"} → domain; DELETE /{domain}; PATCH /{domain}/php-version {php_version?}

## dns — /api/v1/dns
- POST /zones [ADMIN] {domain}; GET /zones/{domain}/records [DOM] → {zone,records:[rrsets]}
- PUT /zones/{domain}/records [DOM] {domain,subdomain="@",type,values:[],ttl=3600}
- DELETE /zones/{domain}/records [DOM] ?subdomain&type

## databases — /api/v1/accounts/{username}/databases [ACCT]
- GET → {databases:[{id,account_id,db_name,db_user,created_at}]}
- POST {name,password?}; DELETE /{name}; POST|PATCH /{name}/password {password?} → {db_name,db_user,password}

## mail — /api/v1/mail + /api/v1/accounts/{username}/email
- POST /mail/domains [DOM] {domain}; POST /mail/mailboxes [DOM] {domain,local_part,password,quota_mb=1024}
- GET /mail/domains/{domain}/mailboxes [DOM] → {mailboxes:[{local_part,...}]}
- DELETE /mail/domains/{domain}/mailboxes/{local_part} [DOM]
- PATCH /accounts/{username}/email/{local_part}/password [ACCT+DOM] {domain,password}

## email — /api/v1/accounts/{username}/domains/{domain}/email [ACCT+DOM]
- GET|POST|DELETE /forwarders {local_part,destination}
- GET|POST|DELETE /catchall {destination}
- GET /autoresponders/{local_part}; POST /autoresponders {local_part,subject,body,start_date?,end_date?}; DELETE /autoresponders/{local_part}
- GET|PATCH /spam-filter {enabled=true,threshold?} → {domain,enabled,threshold,effective_threshold}
- /api/v1/spamfilter/global-default [ADMIN] GET|PATCH {threshold}

## ssl — /api/v1/ssl + /api/v1/accounts/{username}
- POST /ssl/issue [DOM] {domain}; GET /ssl/{domain}/status [DOM]
- GET /accounts/{username}/ssl [ACCT] → {username,certbot_timer_active,domains:[{domain,ssl_status,is_wildcard,cert_status,expiry_date,days_remaining,issuer,auto_renew}]}
- POST /accounts/{username}/domains/{domain}/ssl/issue [ACCT+DOM] {force=false}
- POST /accounts/{username}/domains/{domain}/ssl/wildcard [ACCT+DOM] {force=false}

## backups — /api/v1/backups [ADMIN]
- GET|POST /destinations {name,kind="local",local_path?,rclone_remote_type?,rclone_config={},rclone_path_prefix=""}; DELETE /destinations/{id}
- GET|POST /schedules {username?,frequency="daily",retention_count=7,destination_id,enabled=true}
- GET /jobs ?username → {jobs:[...]}

## account_backups — /api/v1/accounts/{username}/backups [ACCT]
job={id,account_id,kind,item_ref,status,trigger,destination_id,artifact_path,size_bytes,progress_message,error,started_at,completed_at}
- POST {kind="full",item_ref?,destination_id?}; GET → {jobs:[job]}; GET /{job_id}; GET /{job_id}/browse
- POST /{job_id}/restore {kind?,item_ref?}; GET /restores/list

## cron — /api/v1/accounts/{username}/crons [ACCT]
- GET|PATCH /mailto {mailto=""}; GET → {jobs:[...]}; POST {schedule,command,label=""}; PUT /{job_id}; DELETE /{job_id}

## ftp — /api/v1/accounts/{username}/ftp [ACCT]
- GET → {ftp_accounts:[{id,ftp_login,label,path,created_at}]}; POST {label,password,path=""}
- PATCH /{label} {path}; PATCH /{label}/password {password}; DELETE /{label}

## sshkeys — /api/v1/accounts/{username}/ssh-keys [SELF]
- GET → {keys:[{bits,fingerprint,comment,type}]}; POST {key}; DELETE /{fingerprint}

## git — /api/v1/accounts/{username}/git [ACCT]
- GET → {repos:[{name,deploy_target,created_at}]}; POST {name}; DELETE /{name}
- PATCH /{name}/deploy-target {deploy_target}; GET /{name}/push-log → {lines:[]}

## redirects — /api/v1/accounts/{username}/domains/{domain}/redirects [ACCT+DOM]
- GET → {redirects:[{id,path,target_url,status_code,created_at}]}; POST {path,target_url,status_code=301}; PUT; DELETE ?path

## php_ini — /api/v1/accounts/{username}/php-ini [ACCT]
- GET → {php_ini:{memory_limit,upload_max_filesize,post_max_size,max_execution_time,display_errors,error_reporting}|null, defaults:{}}
- PATCH {...fields}; DELETE (reset)

## usage — /api/v1/accounts/{username}/usage [ACCT]
- GET ?force_refresh=false → {current:{disk_home_bytes,disk_mail_bytes,disk_db_bytes,disk_total_bytes,inode_count,process_count,taken_at}, quota_soft_mb, quota_hard_mb, history:[], bandwidth_daily:[{date,bytes_served}], bandwidth_month_to_date_bytes}

## bandwidth — /api/v1/accounts/{username}/bandwidth [ACCT] + /api/v1/admin/bandwidth
- GET ?period=daily → {username,period,buckets:[{label,bytes_served}],top_domains:[{domain,bytes_served}],total_bytes_served}
- GET /admin/bandwidth/ranking [ADMIN] ?period=monthly → {period,ranking:[...]}

## usage_alerts — /api/v1/accounts/{username}/usage-limits + /alerts
- GET /usage-limits [ACCT] / PATCH [ADMIN] {bandwidth_limit_mb?,database_limit?,email_account_limit?,subdomain_limit?,auto_suspend_at_100?}
- GET /alerts [ACCT] → {alerts:[{id,resource,threshold_pct,triggered_at,resolved_at,acknowledged}],active:[...]}

## notifications — /api/v1/admin/notifications + /api/v1/accounts/{username}/notification-prefs
- GET|PATCH /admin/notifications/settings [ADMIN] {sender_address="",events?:{event:bool}}
- GET|PATCH /accounts/{username}/notification-prefs [ACCT] {customer_email?,events?:{event:bool}}

## webhooks — /api/v1/admin/webhooks [ADMIN]
- POST {url,events:[],secret?,enabled=true}; GET → {webhooks:[{id,url,events,enabled,created_at}]}
- GET|PATCH|DELETE /{id}; GET /{id}/deliveries → {deliveries:[{id,event,payload,status,response_code,attempt_count,error,created_at,last_attempted_at}]}; POST /{id}/test

## services — /api/v1/services [ADMIN]
- GET → {services:[{active,enabled,...}]}; GET /{service} → {service,unit,active,enabled,log_lines:[]}; POST /{service}/{action} {confirm=false}

## health — /api/v1/health [ADMIN]
- GET → {cpu_pct,cpu_count,mem_total,mem_used,mem_pct,disks:[{mount,device,fstype,total,used,free,pct}],net_rx_bytes,net_tx_bytes,uptime_seconds}
- GET /history ?hours=24 → {points:[{taken_at,cpu_pct,mem_used_bytes,mem_total_bytes,net_rx_delta,net_tx_delta}]}

## firewall — /api/v1/firewall [ADMIN]
- GET /rules → {rules:[{rule_id,action,port,protocol,from,comment,protected}],active}; POST /rules {action,port,protocol="any",from_addr="any",comment=""}; DELETE /rules/{rule_id}
- GET /status; POST /enable|/disable {confirm=false}

## fail2ban — /api/v1/fail2ban [ADMIN]
jail={jail,currently_failed,total_failed,currently_banned,total_banned,banned_ips:[]}
- GET → {jails:[jail]}; GET /events ?jail&limit=50; POST /bootstrap; GET /{jail}; POST /{jail}/unban {ip}; POST /{jail}/unban-all

## mailqueue — /api/v1/mail-queue [ADMIN]
- GET ?search → {entries:[{queue_id,status,sender,recipient,size,arrival,age_seconds,defer_reason}],count}
- POST /flush; POST /{queue_id}/flush; POST /{queue_id}/delete; POST /delete-all

## auditlog — /api/v1/audit-log [ADMIN]
- GET ?actor,op,result,target,q,page=1,page_size=50 → {entries:[{id,created_at,actor,role,op,target,params,result,detail}],total,page,page_size}
- GET /export.csv ?filters → text/csv

## waf — /api/v1/waf [ADMIN]
- GET → {available,enabled,domain_overrides:[],custom_rules:[]}; POST /enable {enabled}; POST /domain-override {domain,disabled=true}
- POST /custom-rules {domain,target,pattern}; DELETE /custom-rules/{rule_id}; GET /blocked-requests ?domain&limit=50

## slowquery — /api/v1/mysql/slow-queries [ADMIN]
- GET /status → {enabled,long_query_time,log_output,config_managed}; POST /bootstrap {confirm=false}
- GET ?db&q&limit=100 → {queries:[{start_time,user_host,query_time_seconds,lock_time_seconds,rows_sent,rows_examined,db,sql_text,thread_id}]}

## ipwhitelist — /api/v1/security/ip-whitelist [ADMIN]
- GET → {entries:[{id,value,note}]}; POST {value,note=""}; DELETE /{entry_id}

## cpanel_import — /api/v1/admin/import/cpanel [ADMIN] (multipart)
job={id,username,source,status,progress_message,results,error,started_at,completed_at}
- POST form {username,url?,file?}; GET /{job_id} ?username; GET ?username → {jobs:[job]}

## staging — /api/v1/accounts/{username}/domains/{domain}/staging [ACCT+DOM]
staging={id,source_domain,staging_domain,staging_url,is_wordpress,db_name,created_at,last_synced_at,exists}
- POST; GET (→ staging OR {exists:false}); POST /sync; DELETE

## nodeapps — /api/v1/accounts/{username}/apps/node [ACCT]
app={id,domain,name,entry_point,port,node_version,env_vars,enabled,app_dir,unit,active,unit_enabled,...}
- GET → {apps:[app]}; POST {domain,name,entry_point,node_version?,env_vars={}}; GET|PATCH|DELETE /{app_id}
- POST /{app_id}/start|stop|restart|npm-install; GET /{app_id}/logs → {log_lines:[]}

## pythonapps — /api/v1/accounts/{username}/apps/python [ACCT]
app={id,domain,name,entry_point,app_type,port,env_vars,enabled,venv_dir,unit,active,...}
- GET → {apps:[app]}; POST {domain,name,entry_point,app_type="wsgi",env_vars={}}; GET|PATCH|DELETE /{app_id}
- POST /{app_id}/start|stop|restart|pip-install; GET /{app_id}/logs

## redis — /api/v1/accounts/{username}/redis [ACCT]
- GET → status{id,mem_mb,enabled,socket_path,used_memory_human,provisioned} OR {enabled:false,provisioned:false}
- POST {mem_mb?}; PATCH {mem_mb}; DELETE; POST /flush; GET /connection-info

## lscache — /api/v1/accounts/{username}/domains/{domain}/lscache [ACCT+DOM]
- GET → {domain,enabled,ttl_seconds,exclude_paths:[],last_purged_at}; PUT {enabled,ttl_seconds=3600,exclude_paths=[]}; POST /purge; GET /stats
