package com.sense2act.backend.api;

import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.domain.investigation.EvidenceService;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

import java.nio.file.Files;

/** 证据详情与快照(E4-5):读接口任意登录角色。快照按抓取原样回字节。 */
@RestController
public class EvidenceController {

    private final EvidenceService evidenceService;

    public EvidenceController(EvidenceService evidenceService) {
        this.evidenceService = evidenceService;
    }

    @GetMapping("/api/v1/evidences/{id}")
    public ApiResponse<EvidenceService.EvidenceView> detail(@PathVariable String id) {
        return ApiResponse.ok(evidenceService.detail(id));
    }

    @GetMapping("/api/v1/evidences/{id}/snapshot")
    public ResponseEntity<byte[]> snapshot(@PathVariable String id) throws Exception {
        return ResponseEntity.ok()
                .contentType(MediaType.APPLICATION_OCTET_STREAM)
                .body(Files.readAllBytes(evidenceService.snapshotFile(id)));
    }
}
