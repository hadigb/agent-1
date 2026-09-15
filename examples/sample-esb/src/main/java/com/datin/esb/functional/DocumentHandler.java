package com.datin.esb.functional;

import com.datin.esb.dto.IssueDocumentRequest;
import com.datin.esb.dto.IssueDocumentResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.server.ServerRequest;
import org.springframework.web.reactive.function.server.ServerResponse;
import reactor.core.publisher.Mono;

@Component
public class DocumentHandler {
    /** دریافت سند با شناسه */
    public Mono<ServerResponse> get(ServerRequest request) {
        String id = request.pathVariable("id");
        return ServerResponse.ok().body(Mono.just(new IssueDocumentResponse()), IssueDocumentResponse.class);
    }

    /** ایجاد سند (نسخه reactive) */
    public Mono<ServerResponse> create(ServerRequest request) {
        return request.bodyToMono(IssueDocumentRequest.class)
                .flatMap(body -> ServerResponse.ok().body(Mono.just(new IssueDocumentResponse()), IssueDocumentResponse.class));
    }
}
