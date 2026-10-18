package com.sense2act.backend.readiness;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.config.AppProperties;
import com.sense2act.backend.config.JwtTokens;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import com.sense2act.backend.domain.investigation.*;
import com.sense2act.backend.domain.report.ReportService;
import com.sense2act.backend.domain.signal.SignalService;
import com.sense2act.backend.ingest.DocumentIngestService;
import com.sense2act.backend.support.TestDocuments;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.*;
import java.util.concurrent.*;
import static org.assertj.core.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@SpringBootTest(properties = {"app.internal-key=readiness-key", "app.internal-rate-limit-per-minute=0",
        "app.snapshot-dir=${java.io.tmpdir}/sense2act-readiness"})
@AutoConfigureMockMvc
@Testcontainers
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_CLASS)
class ReadinessRegressionTest {
    @Container @ServiceConnection
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("pgvector/pgvector:pg16");
    @Autowired JdbcTemplate jdbc;
    @Autowired InvestigationService investigations;
    @Autowired InvestigationMapper investigationMapper;
    @Autowired DocumentIngestService ingest;
    @Autowired DocumentMapper documents;
    @Autowired EvidenceService evidence;
    @Autowired SignalService signals;
    @Autowired MockMvc mvc;
    @Autowired JwtTokens tokens;
    @Autowired AppProperties props;
    @Autowired ObjectMapper json;
    @Autowired PlatformTransactionManager transactionManager;

    String document(boolean date, boolean snapshot) {
        Document d = new Document();
        d.setId(IdGen.next("doc")); d.setDocType("announcement"); d.setTitle(d.getId());
        d.setContentText("采购公告 " + d.getId()); d.setUrl("http://readiness.test/" + d.getId());
        d.setContentHash(d.getId()); d.setAmount(new BigDecimal("100.00"));
        if (date) d.setPublishDate(LocalDate.now());
        if (snapshot) TestDocuments.snapshot(d, props.snapshotDir());
        documents.insert(d); return d.getId();
    }
    String investigation(int budget) {
        String doc = document(true, true), sig = IdGen.next("sig"), inv = IdGen.next("inv");
        jdbc.update("INSERT INTO signals(id, document_id, detector, hits, score, status) VALUES (?, ?, 'test', '[]', 0.5, 'investigating')", sig, doc);
        jdbc.update("INSERT INTO investigations(id, signal_id, status, token_budget) VALUES (?, ?, 'investigating', ?)", inv, sig, budget);
        jdbc.update("UPDATE signals SET investigation_id=? WHERE id=?", inv, sig);
        return inv;
    }
    String source() {
        String id = IdGen.next("src");
        jdbc.update("INSERT INTO sources(id,name,type,url) VALUES (?, ?, 'web_page', 'http://readiness.test')", id, id);
        return id;
    }
    Map<String,Object> item(String key) {
        return new HashMap<>(Map.of("url", "http://readiness.test/" + key, "doc_type", "announcement",
                "title", key, "content_text", key, "publish_date", LocalDate.now().toString(), "amount", "100.00"));
    }
    void rejected(Runnable action, int code) {
        assertThatThrownBy(action::run).isInstanceOfSatisfying(BusinessException.class,
                ex -> assertThat(ex.errorCode().code()).isEqualTo(code));
    }

    @Test void budgetOverflowCannotChangeAccountingOrSteps() {
        String inv = investigation(60000);
        investigations.appendStep(inv,"round_started",1,Map.of(),1);
        int before = jdbc.queryForObject("SELECT count(*) FROM investigation_steps WHERE investigation_id=?",Integer.class,inv);
        rejected(() -> investigations.appendStep(inv,"reflection_updated",1,Map.of(),Integer.MAX_VALUE),42201);
        assertThat(jdbc.queryForObject("SELECT token_used FROM investigations WHERE id=?",Integer.class,inv)).isEqualTo(1);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM investigation_steps WHERE investigation_id=?",Integer.class,inv)).isEqualTo(before);
    }

    @Test void concurrentStepsRespectBudgetAndKeepEveryToken() throws Exception {
        String inv=investigation(20);
        try (ExecutorService pool=Executors.newFixedThreadPool(8)) {
            List<Callable<Boolean>> jobs=new ArrayList<>();
            for(int i=0;i<12;i++) jobs.add(() -> {
                try { investigations.appendStep(inv,"tool_completed",1,Map.of("ok",true),5); return true; }
                catch(BusinessException ex) { assertThat(ex.errorCode().code()).isEqualTo(42201); return false; }
            });
            int successes=0;
            for(Future<Boolean> f:pool.invokeAll(jobs)) if(f.get(15,TimeUnit.SECONDS)) successes++;
            assertThat(successes).isEqualTo(4);
        }
        assertThat(jdbc.queryForObject("SELECT token_used FROM investigations WHERE id=?",Integer.class,inv)).isEqualTo(20);
        assertThat(jdbc.queryForObject("SELECT sum(token_usage) FROM investigation_steps WHERE investigation_id=?",Integer.class,inv)).isEqualTo(20);
        assertThat(jdbc.queryForObject("SELECT count(DISTINCT seq) FROM investigation_steps WHERE investigation_id=?",Integer.class,inv)).isEqualTo(8);
    }

    @Test void waitingWriterRechecksStateAfterStop() throws Exception {
        String inv=investigation(100); CountDownLatch locked=new CountDownLatch(1), release=new CountDownLatch(1);
        try(ExecutorService pool=Executors.newFixedThreadPool(2)) {
            Future<?> stopper=pool.submit(() -> new TransactionTemplate(transactionManager).execute(tx -> {
                investigationMapper.lockById(inv); locked.countDown();
                try { if(!release.await(10,TimeUnit.SECONDS)) throw new IllegalStateException("timeout"); }
                catch(InterruptedException ex) { throw new IllegalStateException(ex); }
                investigations.stop(inv); return null;
            }));
            assertThat(locked.await(10,TimeUnit.SECONDS)).isTrue();
            Future<?> writer=pool.submit(() -> rejected(() -> investigations.appendStep(inv,"tool_completed",1,Map.of(),10),40901));
            release.countDown(); stopper.get(15,TimeUnit.SECONDS); writer.get(15,TimeUnit.SECONDS);
        }
        assertThat(jdbc.queryForObject("SELECT token_used FROM investigations WHERE id=?",Integer.class,inv)).isZero();
        assertThat(jdbc.queryForObject("SELECT count(*) FROM investigation_steps WHERE investigation_id=? AND type='tool_call'",Integer.class,inv)).isZero();
    }

    @Test void questionWritesAreRejectedForAllTerminalStates() {
        for(String status:List.of("stopped","completed","failed")) {
            String inv=investigation(100), q=investigations.createQuestion(inv,"核对原文",1).id();
            jdbc.update("UPDATE investigations SET status=? WHERE id=?",status,inv);
            rejected(() -> investigations.updateQuestion(q,"clarified","迟到的回答",List.of()),40901);
            assertThat(jdbc.queryForObject("SELECT status FROM questions WHERE id=?",String.class,q)).isEqualTo("open");
        }
    }

    @Test void badAmountsAndDatabaseErrorsDoNotRollbackOtherItems() {
        String source=source(), key=IdGen.next("batch");
        Map<String,Object> overflow=item(key+"-overflow"); overflow.put("amount","1000000000000000000000000000000");
        Map<String,Object> badDatabase=item(key+"-db"); badDatabase.put("title","readiness-db-error-"+key);
        jdbc.execute("CREATE FUNCTION readiness_reject() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                + "IF NEW.title LIKE 'readiness-db-error-%' THEN RAISE EXCEPTION 'simulated storage error'; END IF; RETURN NEW; END $$");
        jdbc.execute("CREATE TRIGGER readiness_reject BEFORE INSERT ON documents FOR EACH ROW EXECUTE FUNCTION readiness_reject()");
        try {
            var result=ingest.push(source,List.of(item(key+"-a"),overflow,badDatabase,item(key+"-b")));
            assertThat(result.accepted()).isEqualTo(2); assertThat(result.failed()).isEqualTo(2);
            assertThat(jdbc.queryForObject("SELECT count(*) FROM documents WHERE source_id=?",Integer.class,source)).isEqualTo(2);
            assertThat(result.documentIds()).allSatisfy(id -> assertThat(documents.selectById(id)).isNotNull());
        } finally {
            jdbc.execute("DROP TRIGGER readiness_reject ON documents");
            jdbc.execute("DROP FUNCTION readiness_reject()");
        }
    }

    @Test void concurrentDuplicateCrawlsCountExactlyOneAcceptedDocument() throws Exception {
        String source=source(); var item=item(IdGen.next("duplicate")); item.put("org_name","并发机构"+source);
        item.put("category","医疗信息化");
        try(ExecutorService pool=Executors.newFixedThreadPool(8)) {
            List<Callable<DocumentIngestService.BatchResult>> jobs=new ArrayList<>();
            for(int i=0;i<8;i++) jobs.add(() -> ingest.push(source,List.of(item)));
            int accepted=0, duplicates=0;
            for(var f:pool.invokeAll(jobs)) { var r=f.get(15,TimeUnit.SECONDS); accepted+=r.accepted(); duplicates+=r.duplicates(); assertThat(r.failed()).isZero(); }
            assertThat(accepted).isEqualTo(1); assertThat(duplicates).isEqualTo(7);
        }
        assertThat(jdbc.queryForObject("SELECT count(*) FROM documents WHERE source_id=?",Integer.class,source)).isEqualTo(1);
    }

    @Test void missingEvidenceAndIncompleteDocumentsAreRejected() throws Exception {
        String inv=investigation(100);
        mvc.perform(post("/api/v1/internal/investigations/{id}/report",inv).header("X-Internal-Key","readiness-key")
                .contentType("application/json").content("{\"title\":\"无依据报告\",\"claims\":[{\"text\":\"事实\",\"nature\":\"fact\",\"confidence\":0.9,\"evidence_ids\":[]}]}"))
                .andExpect(status().isUnprocessableEntity()).andExpect(jsonPath("$.code").value(42201));
        for(boolean[] options:List.of(new boolean[]{false,true},new boolean[]{true,false})) {
            String doc=document(options[0],options[1]);
            rejected(() -> evidence.register(inv,"announcement",doc,null,null,"摘录",null),42201);
        }
        assertThat(jdbc.queryForObject("SELECT count(*) FROM reports WHERE investigation_id=?",Integer.class,inv)).isZero();
        assertThat(jdbc.queryForObject("SELECT count(*) FROM evidences WHERE investigation_id=?",Integer.class,inv)).isZero();
    }

    @Test void corsAcceptsAllowedPreflightButKeepsAuthentication() throws Exception {
        mvc.perform(options("/api/v1/documents").header("Origin","http://localhost:5173")
                        .header("Access-Control-Request-Method","GET").header("Access-Control-Request-Headers","authorization"))
                .andExpect(status().isOk()).andExpect(header().string("Access-Control-Allow-Origin","http://localhost:5173"));
        mvc.perform(options("/api/v1/documents").header("Origin","http://untrusted.test")
                        .header("Access-Control-Request-Method","GET"))
                .andExpect(status().isForbidden());
        mvc.perform(get("/api/v1/documents").header("Origin","http://localhost:5173"))
                .andExpect(status().isUnauthorized());
    }

    @Test void machineCanReadActiveRulesAndAmountsAreStrings() throws Exception {
        String doc=document(true,true);
        mvc.perform(get("/api/v1/internal/signal-rules").header("X-Internal-Key","readiness-key"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data[0].params").exists())
                .andExpect(jsonPath("$.data[0].version").isNumber());
        mvc.perform(get("/api/v1/internal/signal-rules").header("X-Internal-Key","wrong"))
                .andExpect(status().isUnauthorized());
        var entry=signals.detectionQueue(100).stream().filter(d -> doc.equals(d.document().id())).findFirst().orElseThrow();
        assertThat(entry.document().amount()).isEqualTo("100.00");
        assertThat(entry.rules()).isNotEmpty().allSatisfy(r -> assertThat(r.enabled()).isTrue());
    }

    @Test void notificationJournalRollsBackWithItsBusinessWrite() {
        String doc=document(true,true), detector=IdGen.next("detector");
        long before=jdbc.queryForObject("SELECT count(*) FROM global_events",Long.class);
        assertThatThrownBy(() -> new TransactionTemplate(transactionManager).execute(tx -> {
            signals.push(doc,List.of(Map.of("rule_type","amount_anomaly")),new BigDecimal("0.5"),detector);
            assertThat(jdbc.queryForObject("SELECT count(*) FROM global_events",Long.class)).isEqualTo(before+1);
            throw new IllegalStateException("force rollback");
        })).isInstanceOf(IllegalStateException.class);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM global_events",Long.class)).isEqualTo(before);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM signals WHERE detector=?",Integer.class,detector)).isZero();
        signals.push(doc,List.of(Map.of("rule_type","amount_anomaly")),new BigDecimal("0.5"),detector);
        assertThat(jdbc.queryForObject("SELECT count(*) FROM global_events",Long.class)).isEqualTo(before+1);
    }

    @Test void stepRetriesRemainIdempotentAcrossRecoveryAndTerminalState() throws Exception {
        String inv=investigation(100);
        var first=investigations.appendStep(inv,"tool_completed",1,Map.of("event","cannot_override","ok",true),10,"stable-request");
        var retry=investigations.appendStep(inv,"tool_completed",1,Map.of("ok",true,"event","cannot_override"),10,"stable-request");
        assertThat(retry.seq()).isEqualTo(first.seq()); assertThat(retry.content().get("event")).isEqualTo("tool_completed");
        rejected(() -> investigations.appendStep(inv,"tool_completed",1,Map.of("ok",false),10,"stable-request"),40901);
        mvc.perform(get("/api/v1/internal/investigations/{id}/steps",inv).header("X-Internal-Key","readiness-key"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.items[0].request_id").value("stable-request"));
        investigations.stop(inv);
        investigations.appendStep(inv,"tool_completed",1,Map.of("ok",true,"event","cannot_override"),10,"stable-request");
        assertThat(jdbc.queryForObject("SELECT token_used FROM investigations WHERE id=?",Integer.class,inv)).isEqualTo(10);
    }
}
