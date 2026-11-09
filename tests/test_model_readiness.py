"""같은 connected component라는 이유만으로 Item-based 양의 점수를 가정하지 않는지 검사."""
import sys
import unittest
from pathlib import Path

import duckdb

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import check_model_readiness as readiness


class ModelReadinessTest(unittest.TestCase):
    def test_direct_positive_support_differs_from_transitive_connectivity(self):
        con=duckdb.connect()
        self.addCleanup(con.close)
        con.execute("CREATE TABLE dp(u INTEGER,i INTEGER,listens INTEGER)")
        # user1과 user2는 item10, user2와 user3는 item20을 공유하며 user4는 고립돼 있다.
        con.executemany("INSERT INTO dp VALUES(?,?,?)",[(1,10,1),(2,10,2),(2,20,1),
                                                       (3,20,1),(3,30,1),(4,40,1)])
        con.execute("CREATE TABLE fixture_targets(u INTEGER,i INTEGER)")
        con.executemany("INSERT INTO fixture_targets VALUES(?,?)",[(1,20),(1,30),(4,20)])
        readiness.build_overlap(con)
        found={(r['u'],r['i']) for r in readiness.supported_targets(con,'fixture_targets')}
        self.assertEqual(found,{(1,20)})
        graph=readiness.components(con)
        self.assertEqual(graph['components'],2)
        self.assertEqual(graph['largest_component_users'],3)
        self.assertEqual(graph['isolated_users'],1)


if __name__=='__main__':
    unittest.main()
