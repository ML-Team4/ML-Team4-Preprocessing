"""학습 신호·평가 난도·자원 위험 진단. 모델 학습/추천/성능 평가를 수행하지 않는다.

기존 audit_raw_data의 고정 규칙을 재사용한다. 모든 대규모 테이블은 임시 DB에만
존재하며 집계·제한 표본만 JSON으로 저장한다. 보고서 해석은 model-readiness-review.md.
"""
from __future__ import annotations

import argparse
import json
import platform
import resource
import sys
import tempfile
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import audit_raw_data as base


def build_overlap(con):
    """사용자끼리 공유하는 학습 항목 수. item×item 전체 행렬은 만들지 않는다."""
    con.execute("""CREATE TABLE user_overlap AS SELECT a.u, b.u AS v, count(*) AS shared_items
        FROM dp a JOIN dp b USING(i) WHERE a.u <> b.u GROUP BY a.u,b.u""")


def supported_targets(con, target_table):
    """한 학습 곡과 목표 곡 사이에 양의 cosine similarity가 가능한 정답 쌍.

    target_table은 코드에서 정한 내부 테이블명만 받는다. positive log-count나 binary
    곡 벡터에서 공유 청취자가 존재하는 필요충분조건이며 Top-K 절삭 전 조건이다.
    """
    if target_table not in {"val_targets", "test_targets", "fixture_targets"}:
        raise ValueError("허용되지 않은 내부 테이블 이름입니다")
    return base.rows(con, f"""SELECT DISTINCT t.u,t.i FROM {target_table} t
        JOIN dp p ON p.i=t.i JOIN user_overlap o ON o.u=t.u AND o.v=p.u""")


def materialize_supported_targets(con, target_table, output_table):
    if (target_table,output_table) not in {("val_targets","val_supported"),("test_targets","test_supported")}:
        raise ValueError("허용되지 않은 내부 테이블 이름입니다")
    con.execute(f"""CREATE TABLE {output_table} AS SELECT DISTINCT t.u,t.i FROM {target_table} t
        JOIN dp p ON p.i=t.i JOIN user_overlap o ON o.u=t.u AND o.v=p.u""")


def components(con):
    """항목별 최소 사용자와의 별 모양 간선을 스트리밍해 connected component만 계산한다."""
    parent = {r[0]:r[0] for r in con.execute("SELECT DISTINCT u FROM dp").fetchall()}
    def find(x):
        while parent[x]!=x:
            parent[x]=parent[parent[x]]
            x=parent[x]
        return x
    cursor=con.execute("""SELECT DISTINCT p.u,o.owner FROM dp p JOIN
        (SELECT i,min(u) AS owner FROM dp GROUP BY i) o USING(i) WHERE p.u<>o.owner""")
    while batch:=cursor.fetchmany(10000):
        for a,b in batch:
            a,b=find(a),find(b)
            if a!=b:
                parent[max(a,b)]=min(a,b)
    sizes={}
    for u in parent:
        root=find(u)
        sizes[root]=sizes.get(root,0)+1
    return {"components":len(sizes),"largest_component_users":max(sizes.values()),
            "isolated_users":sum(n==1 for n in sizes.values()),
            "component_sizes_desc":sorted(sizes.values(),reverse=True)}


def diagnose(con, baseline, sample_size=5):
    print("[readiness] 임시 ID와 train 분포 집계",flush=True)
    con.execute("""CREATE TABLE di AS SELECT row_number() OVER (ORDER BY artist_name,recording_name)-1 AS i,
        artist_name,recording_name,listeners FROM catalog""")
    con.execute("""CREATE TABLE dp AS SELECT c.user_index AS u,d.i,t.listens FROM train_pairs t
        JOIN di d USING(artist_name,recording_name) JOIN cohort c USING(user_id)""")
    con.execute("""CREATE TABLE du AS SELECT u,count(*) AS items,sum(listens) AS events,
        max(listens) AS max_pair_listens FROM dp GROUP BY u""")
    train = base.one(con,"""SELECT count(*) AS users,sum(items) AS pairs,sum(events) AS events,
        min(items) AS min_user_items,quantile_cont(items,0.5) AS median_user_items,
        quantile_cont(items,0.95) AS p95_user_items,max(items) AS max_user_items,
        min(events) AS min_user_events,quantile_cont(events,0.5) AS median_user_events,
        quantile_cont(events,0.95) AS p95_user_events,max(events) AS max_user_events,
        count(*) FILTER (WHERE items<20) AS users_under_20_items,
        count(*) FILTER (WHERE items<50) AS users_under_50_items FROM du""")
    train["items"]=baseline["train"]["items"]
    train["density"]=train["pairs"]/(train["users"]*train["items"])
    train.update(base.one(con,"""SELECT quantile_cont(listens,0.5) AS median_pair_count,
        quantile_cont(listens,0.95) AS p95_pair_count,quantile_cont(listens,0.99) AS p99_pair_count,
        max(listens) AS max_pair_count FROM dp"""))
    support_bands=base.rows(con,"""WITH counts AS (SELECT i,sum(listens) AS events FROM dp GROUP BY i)
        SELECT CASE WHEN listeners=1 THEN '1' WHEN listeners=2 THEN '2' WHEN listeners<=5 THEN '3-5'
        WHEN listeners<=10 THEN '6-10' WHEN listeners<=50 THEN '11-50' ELSE '51+' END AS listeners,
        count(*) AS items,sum(d.listeners) AS user_item_pairs,sum(c.events) AS events
        FROM di d JOIN counts c USING(i) GROUP BY 1 ORDER BY min(d.listeners)""")
    concentration=base.one(con,"""SELECT
        (SELECT sum(events) FROM (SELECT events FROM du ORDER BY events DESC,u LIMIT 1)) AS top1_events,
        (SELECT sum(events) FROM (SELECT events FROM du ORDER BY events DESC,u LIMIT 50)) AS top1pct_events,
        (SELECT sum(events) FROM (SELECT events FROM du ORDER BY events DESC,u LIMIT 500)) AS top10pct_events,
        (SELECT sum(items) FROM (SELECT items FROM du ORDER BY items DESC,u LIMIT 50)) AS top1pct_pairs,
        sum(items::HUGEINT*(items-1)) AS ordered_item_pair_incidences,
        sum(items::HUGEINT*items) AS sum_user_degree_squared FROM du""")
    for key in ["top1_events","top1pct_events","top10pct_events"]:
        concentration[key+"_share"]=concentration[key]/train["events"]
    concentration["top1pct_pairs_share"]=concentration["top1pct_pairs"]/train["pairs"]
    singleton=base.one(con,"""WITH g AS (SELECT p.u,count(*) AS n FROM dp p JOIN di d USING(i)
        WHERE d.listeners=1 GROUP BY p.u) SELECT count(*) AS owners,
        min(n) AS min_items_per_owner,quantile_cont(n,0.5) AS median_items_per_owner,
        max(n) AS max_items_per_owner,count(*) FILTER (WHERE n>10) AS owners_over_10,
        count(*) FILTER (WHERE n>100) AS owners_over_100 FROM g""")
    singleton["top_owner_samples"]=base.rows(con,f"""SELECT p.u AS audit_user_index,count(*) AS singleton_items
        FROM dp p JOIN di d USING(i) WHERE listeners=1 GROUP BY p.u
        ORDER BY singleton_items DESC,p.u LIMIT {sample_size}""")
    singleton["identical_count_columns"]=base.one(con,"""WITH g AS
        (SELECT p.u,p.listens,count(*) AS n FROM dp p JOIN di d USING(i) WHERE listeners=1 GROUP BY p.u,p.listens)
        SELECT count(*) AS distinct_owner_count_patterns,count(*) FILTER (WHERE n>1) AS repeated_patterns,
        sum(n) FILTER (WHERE n>1) AS items_in_repeated_patterns,max(n) AS max_items_identical_column FROM g""")
    print("[readiness] 공유 청취자 graph와 target 근거 집계",flush=True)
    graph_budget=base.one(con,"SELECT sum(listeners::HUGEINT*(listeners-1)) AS directed_pair_incidences FROM di")
    build_overlap(con)
    graph=base.one(con,"""SELECT count(*) AS directed_user_pairs,min(shared_items) AS min_shared,
        quantile_cont(shared_items,0.5) AS median_shared,max(shared_items) AS max_shared,
        count(*) FILTER (WHERE shared_items=1) AS one_shared_item_pairs FROM user_overlap""")
    graph.update(graph_budget)
    graph.update(components(con))
    graph["strong_profile_overlap_samples"]=base.rows(con,f"""SELECT o.u AS first_audit_user_index,
        o.v AS second_audit_user_index,o.shared_items,a.items AS first_items,b.items AS second_items,
        o.shared_items*1.0/(a.items+b.items-o.shared_items) AS jaccard
        FROM user_overlap o JOIN du a ON a.u=o.u JOIN du b ON b.u=o.v
        WHERE o.u<o.v AND o.shared_items>=10 ORDER BY jaccard DESC,o.u,o.v LIMIT {sample_size}""")
    graph["identical_itemset_user_pairs"]=base.one(con,"""SELECT count(*) AS pairs FROM user_overlap o
        JOIN du a ON a.u=o.u JOIN du b ON b.u=o.v WHERE o.u<o.v AND
        o.shared_items=a.items AND a.items=b.items""")["pairs"]
    equal_pairs=con.execute("""SELECT o.u,o.v FROM user_overlap o JOIN du a ON a.u=o.u JOIN du b ON b.u=o.v
        WHERE o.u<o.v AND o.shared_items=a.items AND a.items=b.items ORDER BY o.u,o.v LIMIT ?""",[sample_size]).fetchall()
    equal_samples=[]
    for u,v in equal_pairs:
        detail=base.one(con,"""WITH selected AS (SELECT c.user_index AS u,e.listened_at,e.artist_name,e.recording_name
            FROM events e JOIN cohort c USING(user_id) WHERE c.user_index IN (?,?) AND listened_at<TIMESTAMP '2026-09-28'),
            a AS (SELECT * FROM selected WHERE u=?),b AS (SELECT * FROM selected WHERE u=?)
            SELECT (SELECT count(*) FROM a) AS first_events,(SELECT count(*) FROM b) AS second_events,
            (SELECT count(*) FROM a JOIN b USING(listened_at,artist_name,recording_name)) AS same_timestamp_item_events""",[u,v,u,v])
        equal_samples.append({"first_audit_user_index":u,"second_audit_user_index":v,**detail})
    graph["identical_itemset_timeline_samples"]=equal_samples
    graph["overlap_user_counts"]=base.one(con,"""SELECT min(coalesce(n,0)) AS min_neighbors,
        quantile_cont(coalesce(n,0),0.5) AS median_neighbors,max(coalesce(n,0)) AS max_neighbors
        FROM du LEFT JOIN (SELECT u,count(*) AS n FROM user_overlap GROUP BY u) o USING(u)""")
    con.execute("""CREATE TABLE train_artists AS SELECT DISTINCT c.user_index AS u,artist_name FROM train_pairs
        JOIN cohort c USING(user_id)""")
    evaluation={}
    for prefix,split,start,end in [("val","validation",base.TRAIN_END,base.VALIDATION_END),
                                   ("test","test",base.VALIDATION_END,base.WINDOW_END)]:
        con.execute(f"""CREATE TABLE {prefix}_targets AS SELECT DISTINCT c.user_index AS u,d.i FROM events e
            JOIN cohort c USING(user_id) JOIN di d USING(artist_name,recording_name)
            ANTI JOIN dp p ON p.u=c.user_index AND p.i=d.i
            WHERE listened_at >= ?::TIMESTAMP AND listened_at < ?::TIMESTAMP""",[start,end])
        materialize_supported_targets(con,prefix+"_targets",prefix+"_supported")
        con.execute(f"""CREATE TABLE {prefix}_userstats AS SELECT t.u,count(*) AS targets,
            count(s.i) AS positive_support_targets FROM {prefix}_targets t
            LEFT JOIN {prefix}_supported s USING(u,i) GROUP BY t.u""")
        e=base.one(con,f"""SELECT count(*) AS users,sum(targets) AS target_pairs,
            min(targets) AS min_targets,quantile_cont(targets,0.5) AS median_targets,
            quantile_cont(targets,0.95) AS p95_targets,max(targets) AS max_targets,
            count(*) FILTER (WHERE targets>10) AS users_over_10_targets,
            avg(least(10,targets)*1.0/targets) AS ideal_recall10_ceiling,
            sum(positive_support_targets) AS positive_support_target_pairs,
            avg(positive_support_targets*1.0/targets) AS macro_positive_support_fraction,
            count(*) FILTER (WHERE positive_support_targets=0) AS users_without_supported_targets,
            avg(10.0/((SELECT count(*) FROM di)-d.items)) AS uniform_random_expected_recall10,
            avg(d.items) AS mean_train_items FROM {prefix}_userstats s JOIN du d USING(u)""")
        e["support_bands"]=base.rows(con,f"""SELECT CASE WHEN listeners=1 THEN '1' WHEN listeners=2 THEN '2'
            WHEN listeners<=5 THEN '3-5' WHEN listeners<=10 THEN '6-10'
            WHEN listeners<=50 THEN '11-50' ELSE '51+' END AS train_listeners,count(*) AS target_pairs,
            count(s.i) AS positive_support_pairs FROM {prefix}_targets t JOIN di d USING(i)
            LEFT JOIN {prefix}_supported s USING(u,i) GROUP BY 1 ORDER BY min(listeners)""")
        e.update(base.one(con,f"""SELECT count(*) AS target_pairs_with_new_artist FROM {prefix}_targets t
            JOIN di d USING(i) ANTI JOIN train_artists a USING(u,artist_name)"""))
        e["history_groups"]=base.rows(con,f"""SELECT CASE WHEN d.items<50 THEN '<50' WHEN d.items<200 THEN '50-199'
            WHEN d.items<1000 THEN '200-999' ELSE '1000+' END AS train_items,
            count(*) AS users,avg(s.targets) AS mean_targets,
            avg(positive_support_targets*1.0/targets) AS macro_support FROM {prefix}_userstats s JOIN du d USING(u)
            GROUP BY 1 ORDER BY min(d.items)""")
        b=baseline["evaluation"][split]
        e["new_item_event_fraction"]=b["new_item"]["events"]/b["events"]
        e["known_period_pair_fraction"]=1-b["new_item"]["pairs"]/b["user_item_pairs"]
        e["eligible_cohort_fraction"]=e["users"]/train["users"]
        e["baseline_exclusions"]=b
        if e["target_pairs"]!=b["target_pairs"] or e["users"]!=b["eligible_users"]:
            raise ValueError("추가 집계의 target 수가 원본 점검과 다릅니다")
        evaluation[split]=e
    evaluation["cross_period"]=base.one(con,"""SELECT
        (SELECT count(*) FROM val_targets JOIN test_targets USING(u,i)) AS shared_target_pairs,
        (SELECT count(*) FROM (SELECT DISTINCT u FROM val_targets) v JOIN
        (SELECT DISTINCT u FROM test_targets) t USING(u)) AS shared_eligible_users""")
    print("[readiness] 활동량·이름 중복·기간별 변화 점검",flush=True)
    activity=base.one(con,"""WITH timestamps AS (SELECT user_id,listened_at,count(*) AS n FROM events
        WHERE listened_at < TIMESTAMP '2026-09-28' GROUP BY user_id,listened_at)
        SELECT count(*) AS user_timestamps,count(*) FILTER (WHERE n>1) AS multitrack_timestamps,
        sum(n) FILTER (WHERE n>1) AS events_at_multitrack_timestamps,max(n) AS max_tracks_same_timestamp
        FROM timestamps""")
    activity.update(base.one(con,"""SELECT count(*) FILTER (WHERE events>720) AS active_days_over_720,
        count(*) FILTER (WHERE events>1440) AS active_days_over_1440,
        count(DISTINCT user_id) FILTER (WHERE events>1440) AS users_with_day_over_1440,
        max(events) AS max_day_events FROM user_days WHERE day < DATE '2026-09-28'"""))
    activity.update(base.one(con,"""WITH ts AS (SELECT DISTINCT user_id,listened_at FROM events
        WHERE listened_at < TIMESTAMP '2026-09-28'), gaps AS
        (SELECT epoch(listened_at)-epoch(lag(listened_at) OVER (PARTITION BY user_id ORDER BY listened_at)) AS seconds FROM ts)
        SELECT count(seconds) AS adjacent_distinct_timestamps,
        count(*) FILTER (WHERE seconds<30) AS gaps_under_30s,
        quantile_cont(seconds,0.5) AS median_gap_seconds FROM gaps"""))
    # 동시각의 서로 다른 항목 수는 중복도와 다르다. 아래 구간은 기술통계용이고 삭제 기준이 아니다.
    con.execute("""CREATE TABLE burst_sizes AS SELECT user_id,listened_at,count(*) AS tracks FROM events
        GROUP BY user_id,listened_at""")
    bursts=base.rows(con,"""SELECT CASE WHEN listened_at<TIMESTAMP '2026-09-28' THEN 'train'
        WHEN listened_at<TIMESTAMP '2026-10-05' THEN 'validation' ELSE 'test' END AS split,
        count(*) FILTER (WHERE tracks>=10) AS timestamps_ge10,
        count(DISTINCT user_id) FILTER (WHERE tracks>=10) AS users_ge10,
        coalesce(sum(tracks) FILTER (WHERE tracks>=10),0) AS events_ge10,
        count(*) FILTER (WHERE tracks>=100) AS timestamps_ge100,
        coalesce(sum(tracks) FILTER (WHERE tracks>=100),0) AS events_ge100,
        max(tracks) AS max_tracks FROM burst_sizes GROUP BY split ORDER BY split""")
    activity["burst_samples"]=base.rows(con,f"""SELECT c.user_index AS audit_user_index,b.listened_at,
        b.tracks,count(DISTINCT e.artist_name) AS artists,min(e.artist_name) AS first_artist,
        min(e.recording_name) AS first_title FROM burst_sizes b JOIN cohort c USING(user_id)
        JOIN events e USING(user_id,listened_at) WHERE b.tracks>=10 AND b.listened_at<TIMESTAMP '2026-09-28'
        GROUP BY c.user_index,b.listened_at,b.tracks ORDER BY b.tracks DESC,c.user_index,b.listened_at LIMIT {sample_size}""")
    for prefix,split,start,end in [("val","validation",base.TRAIN_END,base.VALIDATION_END),
                                   ("test","test",base.VALIDATION_END,base.WINDOW_END)]:
        evaluation[split]["targets_with_period_burst_ge10"]=base.one(con,f"""SELECT count(*) AS target_pairs
            FROM {prefix}_targets t SEMI JOIN
            (SELECT DISTINCT c.user_index AS u,d.i FROM events e JOIN burst_sizes b USING(user_id,listened_at)
            JOIN cohort c USING(user_id) JOIN di d USING(artist_name,recording_name)
            WHERE b.tracks>=10 AND listened_at>=?::TIMESTAMP AND listened_at<?::TIMESTAMP) b USING(u,i)""",[start,end])["target_pairs"]
    duplicates=base.rows(con,f"""SELECT c.user_index AS audit_user_index,sum(multiplicity-1) AS excess_rows,
        sum(multiplicity) AS raw_rows,sum(multiplicity-1)*1.0/sum(multiplicity) AS excess_fraction
        FROM duplicate_groups d JOIN cohort c USING(user_id) WHERE listened_at<TIMESTAMP '2026-09-28'
        GROUP BY c.user_index ORDER BY excess_rows DESC,c.user_index LIMIT {sample_size}""")
    names=base.one(con,"""WITH n AS (SELECT DISTINCT artist_name,recording_name FROM raw),
        g AS (SELECT norm_name(artist_name) AS a,norm_name(recording_name) AS r,count(*) AS variants
        FROM n GROUP BY a,r) SELECT count(*) FILTER (WHERE variants>1) AS merged_normalized_keys,
        sum(variants-1) AS reduction_in_raw_name_pairs,max(variants) AS max_raw_variants FROM g""")
    names.update(base.one(con,r"""SELECT
        count(*) FILTER (WHERE contains(artist_name,chr(65533)) OR contains(recording_name,chr(65533))) AS replacement_character_rows,
        count(*) FILTER (WHERE regexp_matches(artist_name||recording_name,'[\x{200B}\x{FEFF}]')) AS zero_width_or_bom_rows,
        count(*) FILTER (WHERE regexp_matches(artist_name||recording_name,'[\x{0000}-\x{0008}\x{000E}-\x{001B}\x{007F}-\x{0084}\x{0086}-\x{009F}]')) AS nonwhitespace_control_rows FROM raw"""))
    names.update(base.one(con,"""WITH variants AS (SELECT DISTINCT artist_name,recording_name FROM events),
        g AS (SELECT lower(artist_name),lower(recording_name),count(*) AS n FROM variants GROUP BY ALL)
        SELECT count(*) FILTER (WHERE n>1) AS lowercase_collision_groups,
        sum(n-1) AS lowercase_extra_pairs FROM g"""))
    weekly=base.rows(con,"""SELECT floor(date_diff('day',DATE '2026-08-17',cast(listened_at AS DATE))/7)::INTEGER+1 AS week,
        count(*) AS events,count(DISTINCT user_id) AS users,
        count(DISTINCT (artist_name,recording_name)) AS items FROM events GROUP BY week ORDER BY week""")
    resource_estimates={"units":"byte 단위, float32 값과 int32 index는 각각 4byte이며 framework 추가 메모리는 별도",
        "dense_user_item_float32_bytes":train["users"]*train["items"]*4,
        "dense_item_item_float32_bytes":train["items"]**2*4,
        "raw_csr_int64_data_int32_structure_bytes":train["pairs"]*12+(train["users"]+1)*4,
        "float32_factors":[{"dimension":d,"bytes":(train["users"]+train["items"])*d*4} for d in [32,64,128,256]],
        "float32_score_batches":[{"users":u,"bytes":u*train["items"]*4} for u in [1,32,128,train["users"]]],
        "item_topk_float32_int32":[{"k":k,"maximum_edge_arrays_bytes":train["items"]*k*8+(train["items"]+1)*4} for k in [10,100]]}
    return dict(train=train,support_bands=support_bands,concentration=concentration,
        singleton_cosine_ties=singleton,user_graph=graph,evaluation=evaluation,
        train_activity=activity,simultaneous_track_bursts=bursts,duplicate_concentration_samples=duplicates,names=names,
        weekly=weekly,resource_estimates=resource_estimates)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",type=Path,required=True)
    args=parser.parse_args()
    config_path=args.config.resolve()
    cfg=tomllib.loads(config_path.read_text())
    paths={k:(config_path.parent/Path(cfg[k]).expanduser()).resolve() for k in ["raw_dir","report_dir","cache_dir"]}
    for k in ["report_dir","cache_dir"]:
        if paths[k]==paths["raw_dir"] or paths["raw_dir"] in paths[k].parents:
            raise ValueError("출력 폴더는 raw_dir 밖에 지정해야 합니다")
    sample=cfg.get("sample_size",5)
    threads=cfg.get("threads",2)
    if not isinstance(sample,int) or not 1<=sample<=20 or not isinstance(threads,int) or not 1<=threads<=4:
        raise ValueError("sample_size 또는 threads가 허용 범위를 벗어났습니다")
    before={n:base.sha256(paths["raw_dir"]/n) for n in base.REQUIRED_FILES}
    if cfg.get("expected_sha256") and before[base.REQUIRED_FILES[0]]!=cfg["expected_sha256"]:
        raise ValueError("입력 hash가 기준과 다릅니다")
    started=time.monotonic()
    paths["cache_dir"].mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="readiness-",dir=paths["cache_dir"]) as work:
        con=duckdb.connect(str(Path(work)/"readiness.duckdb"))
        try:
            con.execute("SET memory_limit = ?",[cfg.get("memory_limit","3GB")])
            con.execute("SET threads = ?",[threads])
            con.execute("SET TimeZone='UTC'")
            con.execute("SET preserve_insertion_order=false")
            con.execute("SET temp_directory = ?",[str(Path(work)/"spill")])
            con.execute("SET max_temp_directory_size='12GiB'")
            baseline=base.audit(con,paths["raw_dir"],sample)
            result=diagnose(con,baseline,sample)
        finally:
            con.close()
    after={n:base.sha256(paths["raw_dir"]/n) for n in base.REQUIRED_FILES}
    if before!=after:
        raise RuntimeError("점검 중 입력이 변경됐습니다")
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result["execution"]={"finished_utc":datetime.now(timezone.utc).isoformat(),"python":platform.python_version(),
        "duckdb":duckdb.__version__,"elapsed_seconds":round(time.monotonic()-started,2),
        "peak_rss_bytes":rss if sys.platform=="darwin" else rss*1024,
        "memory_limit":cfg.get("memory_limit","3GB"),"threads":threads,
        "inputs_unchanged":True,"input_sha256":before,
        "script_sha256":base.sha256(Path(__file__)),"base_script_sha256":base.sha256(Path(base.__file__)),
        "models_trained":False,"model_metrics_measured":False,"final_datasets_created":False}
    paths["report_dir"].mkdir(parents=True,exist_ok=True)
    (paths["report_dir"]/"model-readiness-diagnostics.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+"\n")
    print(json.dumps({"train":result["train"],"user_graph":result["user_graph"],"execution":result["execution"]},ensure_ascii=False,default=str),flush=True)


if __name__=="__main__":
    main()
