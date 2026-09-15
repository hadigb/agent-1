package com.example.tricky;

import java.util.*;
import java.util.function.Function;
import static java.util.Objects.requireNonNull;
import org.springframework.web.bind.annotation.*;

/**
 * Controller for tricky syntax.
 * Second sentence with {@code inline} and {@link Other}.
 *
 * @author someone
 * @since 1.0
 */
@RestController
@RequestMapping(value = {"/API", "/api"}, produces = MediaType.APPLICATION_JSON_VALUE)
public class Tricky<T extends Comparable<T>> extends Base<Map<String, List<Integer>>> implements Runnable, java.io.Serializable {

    private static final String BASE = "/x" + "/y";
    // شناسه ترمینال
    @NotNull private Map<String, List<Integer>> map = new HashMap<String, List<Integer>>(), other = null;
    private int[] arr = {1, 2, 3}, matrix[][];
    private final Function<String, Integer> fn = s -> s.length() < 3 ? 1 : 2; // trailing comment
    protected List<? extends Number> nums;
    private Runnable r = new Runnable() { public void run() { int a = 1 < 2 ? 3 : 4; } };
    private String text = """
        hello
          "world"
        """;
    private char c = '\'';
    private long big = 0xFFL;

    static { System.out.println("init"); }

    public Tricky() { this(null); }

    protected Tricky(String s) { super(); requireNonNull(s); }

    /**
     * Issues a document.
     * @param request the request body
     * @param apiKey the api key header
     * @return the result
     * @throws BusinessException when validation fails
     */
    @PostMapping(value = "/IssueDocument", consumes = "application/json")
    public ResponseEntity<ApiResponse<IssueResult>> issue(@Valid @RequestBody IssueRequest request,
                                                          @RequestHeader("ApiKey") String apiKey,
                                                          @RequestParam(value = "verbose", required = false, defaultValue = "false") boolean verbose,
                                                          @PathVariable("id") long id,
                                                          HttpServletRequest raw) throws BusinessException, java.io.IOException {
        if (request.getAmount() < 0) {
            throw new BusinessException(ErrorCode.INVALID_INPUT, "bad");
        }
        String p = raw.getParameter("page");
        List<DocumentItem> items = service.<DocumentItem>convert(request.getItems());
        IssueResult res = documentService.issue(request, TransactionChannel.POS);
        Map.Entry<String, Integer> e = null;
        return ResponseEntity.ok(ApiResponse.success(res));
    }

    public <U> U generic(Function<T, U> f, U... rest) { return f.apply(null); }

    @Override public void run() {}

    public abstract static class Base<X> { protected X value; }

    public enum Channel {
        /** point of sale */
        POS(1, "پایانه فروش"), ATM(2, "خودپرداز") { @Override public String toString() { return "atm"; } },
        // internet banking
        INTERNET(3, "اینترنت"), TELEPHONE(4, "تلفن"); // end

        private final int code;
        private final String title;

        Channel(int code, String title) { this.code = code; this.title = title; }

        public int getCode() { return code; }
    }

    public record Pair<A, B>(@NotNull A first, B second) implements java.io.Serializable {
        public Pair { requireNonNull(first); }
        public static <A, B> Pair<A, B> of(A a, B b) { return new Pair<>(a, b); }
    }

    public interface Handler { void handle(String s); default void other() {} }

    public @interface Marker { String value() default ""; int[] codes() default {}; }

    sealed interface Shape permits Circle, Square {}
    record Circle(double r) implements Shape {}
    record Square(double s) implements Shape {}
}

class PackagePrivate {
    void m() {
        Object o = switch (1) { case 1 -> "a"; default -> "b"; };
        var list = new ArrayList<>();
        list.forEach(x -> { if (x != null) System.out.println(x); });
        Runnable rr = this::m;
        handler.route(GET("/functional"), handler::get);
        Foo.BAR.baz();
        int shifted = 1 >> 2 >>> 3;
    }
}
