/*
 * YCSB binding for the kv-store API (api/openapi.yaml), task 13.2.
 *
 * Why our own binding: YCSB's built-in "rest" binding sends POST for insert, puts only one
 * field ("data") in the body as raw text and never uses the table name. Our API needs
 * PUT /tables/{table}/items with a JSON body that holds the partition key and "value".
 *
 * Mapping (table without a sort key, ADR-007):
 *   insert, update -> PUT    /tables/{table}/items   {"<pk>": key, "value": {field: text}}
 *   read           -> GET    /tables/{table}/items/{key}
 *   delete         -> DELETE /tables/{table}/items/{key}
 *   scan           -> not implemented (workloads A and C do not scan)
 *
 * PUT replaces the whole item, so update is correct only with writeallfields=true
 * (set in bench/params.env): YCSB then sends every field, not just one.
 *
 * Properties: kv.url (coordinator, e.g. http://10.0.11.10:8080), kv.table (default usertable),
 * kv.pk (partition key attribute, default ycsb_key), kv.timeout.ms (default 10000).
 */
package kvstore.ycsb;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.Vector;

import site.ycsb.ByteIterator;
import site.ycsb.DB;
import site.ycsb.DBException;
import site.ycsb.Status;

public class KvStoreClient extends DB {

  // One HTTP client for all YCSB threads: it is thread-safe and keeps connections open
  // (keep-alive), so we do not pay a new TCP handshake per operation.
  private static HttpClient client;

  private String baseUrl;
  private String pkName;
  private Duration timeout;

  @Override
  public void init() throws DBException {
    Properties p = getProperties();
    String url = p.getProperty("kv.url");
    if (url == null || url.isEmpty()) {
      throw new DBException("property kv.url is required, e.g. -p kv.url=http://10.0.11.10:8080");
    }
    String table = p.getProperty("kv.table", "usertable");
    pkName = p.getProperty("kv.pk", "ycsb_key");
    timeout = Duration.ofMillis(Long.parseLong(p.getProperty("kv.timeout.ms", "10000")));
    baseUrl = stripSlash(url) + "/tables/" + encode(table) + "/items";
    synchronized (KvStoreClient.class) {
      if (client == null) {
        // HTTP/1.1 explicitly: the server is http.server (no HTTP/2).
        client = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(2))
            .build();
      }
    }
  }

  @Override
  public Status read(String table, String key, Set<String> fields, Map<String, ByteIterator> result) {
    // The body is read but not parsed: YCSB needs it only for data-integrity checks,
    // which this baseline does not use.
    HttpRequest req = request(baseUrl + "/" + encode(key)).GET().build();
    return send(req, 200);
  }

  @Override
  public Status insert(String table, String key, Map<String, ByteIterator> values) {
    return put(key, values);
  }

  @Override
  public Status update(String table, String key, Map<String, ByteIterator> values) {
    return put(key, values);
  }

  @Override
  public Status delete(String table, String key) {
    HttpRequest req = request(baseUrl + "/" + encode(key)).DELETE().build();
    return send(req, 204);
  }

  @Override
  public Status scan(String table, String startkey, int recordcount, Set<String> fields,
                     Vector<HashMap<String, ByteIterator>> result) {
    return Status.NOT_IMPLEMENTED;
  }

  // PUT /tables/{table}/items with {"<pk>": "<key>", "value": {"field0": "...", ...}}.
  private Status put(String key, Map<String, ByteIterator> values) {
    StringBuilder body = new StringBuilder();
    body.append('{').append(quote(pkName)).append(':').append(quote(key)).append(",\"value\":{");
    boolean first = true;
    for (Map.Entry<String, ByteIterator> e : values.entrySet()) {
      if (!first) {
        body.append(',');
      }
      first = false;
      body.append(quote(e.getKey())).append(':').append(quote(e.getValue().toString()));
    }
    body.append("}}");
    HttpRequest req = request(baseUrl)
        .header("Content-Type", "application/json")
        .PUT(HttpRequest.BodyPublishers.ofString(body.toString(), StandardCharsets.UTF_8))
        .build();
    return send(req, 201);
  }

  private HttpRequest.Builder request(String url) {
    return HttpRequest.newBuilder(URI.create(url)).timeout(timeout);
  }

  // Sends the request and maps the HTTP status to a YCSB status.
  private Status send(HttpRequest req, int expected) {
    try {
      HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
      int code = resp.statusCode();
      if (code == expected) {
        return Status.OK;
      }
      if (code == 404) {
        return Status.NOT_FOUND;
      }
      if (code == 503) {
        return Status.SERVICE_UNAVAILABLE;
      }
      System.err.println(req.method() + " " + req.uri() + " -> " + code + " " + resp.body());
      return Status.ERROR;
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      return Status.ERROR;
    } catch (Exception e) {
      System.err.println(req.method() + " " + req.uri() + " failed: " + e);
      return Status.ERROR;
    }
  }

  // Path segment encoding: URLEncoder writes a space as "+", which in a path means "+",
  // so it is replaced by "%20".
  private static String encode(String s) {
    return URLEncoder.encode(s, StandardCharsets.UTF_8).replace("+", "%20");
  }

  private static String stripSlash(String s) {
    return s.endsWith("/") ? s.substring(0, s.length() - 1) : s;
  }

  // JSON string literal: escape quote, backslash and control characters.
  private static String quote(String s) {
    StringBuilder sb = new StringBuilder(s.length() + 2);
    sb.append('"');
    for (int i = 0; i < s.length(); i++) {
      char c = s.charAt(i);
      if (c == '"' || c == '\\') {
        sb.append('\\').append(c);
      } else if (c < 0x20) {
        sb.append(String.format("\\u%04x", (int) c));
      } else {
        sb.append(c);
      }
    }
    return sb.append('"').toString();
  }
}
