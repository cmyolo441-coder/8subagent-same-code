"""HTTP resource isolation tests; only local deterministic HTTP is used."""
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from fullagent.computer.state import ComputerError
from fullagent.computer.transport import SessionPool, TransportError
from tests.test_computer_network import HTTPFixture, client


class SharedSessionTests(unittest.TestCase):
    def test_72_logical_clients_share_one_session_on_one_thread(self):
        pool=SessionPool(2);self.addCleanup(pool.close)
        clients=[client('http://127.0.0.1:12345/v1') for _ in range(72)]
        for c in clients:c.session_pool=pool
        sessions=[c._session() for c in clients]
        self.assertEqual(len({id(s) for s in sessions}),1)
        self.assertEqual(pool.peak,1)
        for c in clients:c.close()
        self.assertFalse(pool.closed)
        self.assertFalse(sessions[0].trust_env)
        self.assertNotIn('Authorization',sessions[0].headers)

    def test_eight_threads_get_eight_isolated_bounded_sessions(self):
        pool=SessionPool(8);self.addCleanup(pool.close)
        barrier=threading.Barrier(8)
        def action():
            session=pool.session();barrier.wait(timeout=5);return id(session)
        with ThreadPoolExecutor(max_workers=8) as executor:
            ids=list(executor.map(lambda _:action(),range(8)))
        self.assertEqual(len(set(ids)),8)
        self.assertEqual(pool.peak,8)

    def test_pool_limit_fails_closed_and_close_prevents_reuse(self):
        pool=SessionPool(1);pool.session()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.assertRaises(ComputerError):executor.submit(pool.session).result(3)
        pool.close()
        self.assertEqual(pool.sessions,{})
        with self.assertRaises(ComputerError):pool.session()

    def test_standalone_client_also_ignores_ambient_auth_and_cookies(self):
        c=client('http://127.0.0.1:12345/v1');self.addCleanup(c.close)
        s=c._session();self.assertFalse(s.trust_env)
        s.cookies.set('session','fixture-cookie')
        self.assertEqual(len(c._session().cookies),0)

    def test_real_local_http_keeps_each_request_auth_separate(self):
        body=json.dumps({'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}],
                         'usage':{'prompt_tokens':3,'completion_tokens':1}}).encode()
        with HTTPFixture([(200,'application/json',body,{'Set-Cookie':'session=fixture-cookie'}),
                          (200,'application/json',body)]) as server:
            pool=SessionPool(1)
            try:
                a,b=client(server.url),client(server.url)
                a.session_pool=b.session_pool=pool
                a.provider=replace(a.provider,api_key='fixture-key-one')
                b.provider=replace(b.provider,api_key='fixture-key-two')
                messages=[{'role':'system','content':'Local test'},{'role':'user','content':'fixture'}]
                self.assertEqual(a.chat(messages,[],512,lambda:None).content,'ok')
                a.close()  # must not close the mission-owned shared pool
                self.assertEqual(b.chat(messages,[],512,lambda:None).content,'ok')
                self.assertEqual(server.received_headers[0]['Authorization'],'Bearer fixture-key-one')
                self.assertEqual(server.received_headers[1]['Authorization'],'Bearer fixture-key-two')
                self.assertNotIn('Cookie',server.received_headers[1])
                self.assertEqual(pool.peak,1)
            finally:pool.close()

    def test_model_post_redirect_is_not_followed(self):
        with HTTPFixture([(307,'application/json',b'{}',{'Location':'http://127.0.0.1:1/unapproved'})]) as server:
            c=client(server.url);self.addCleanup(c.close)
            with self.assertRaises(TransportError) as caught:
                c.chat([{'role':'system','content':'Local test'},{'role':'user','content':'fixture'}],[],512,lambda:None)
            self.assertEqual(caught.exception.status_code,307)
            self.assertEqual(len(server.received),1)

    def test_nonloopback_model_endpoints_require_https(self):
        with self.assertRaisesRegex(ComputerError,'HTTPS'):
            client('http://example.invalid/v1')
